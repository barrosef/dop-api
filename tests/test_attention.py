"""Caixa de atenção nos dois transportes, contra um núcleo falso.

Quatro grupos carregam o arquivo, e cada um protege uma decisão da spec
(conversação-e-atenção §3):

  - **a ordem é do núcleo.** A prioridade é DERIVADA lá (impacto do tipo,
    depois idade) e não se recalcula aqui: uma segunda régua faria a fila
    deixar de ter uma ordem só. O teste manda a fila embaralhada de propósito —
    se a borda ordenasse, ela sairia diferente;
  - **o badge é `open_total`.** Contar a página daria um número que muda quando
    o dev pagina;
  - **o que o contrato NÃO oferece.** Não existe rota nem RPC de "marcar como
    lido" ou "descartar": item nasce e morre de evento, e item que some sem o
    problema resolvido é mentira confortável (risco R-1). Há teste para a
    ausência, porque ausência que ninguém testa alguém "conserta";
  - **paridade** REST × gRPC, o alarme que dispara se o agrupamento ou os
    nomes forem reimplementados num adaptador.

Os duplos e as fixtures vivem NESTE arquivo, e não no `conftest.py`: conftest é
território compartilhado e há outros agentes escrevendo aqui agora. O que já
existe lá (token, metadados, `ChamadaFalsa`, o núcleo de identidade e
`erro_grpc`) é importado.
"""

import asyncio
import json

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import timestamp_pb2
from grpc.aio import AioRpcError

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import attention_pb2, common_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.attention import AttentionServicer
from app.grpcapi.gen.dop.bff.v1 import attention_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import attention_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import attention as rotas
from app.settings import settings
from app.usecases import attention as uc
from tests.conftest import PROJECT, ChamadaFalsa, erro_grpc, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS = {"authorization": token_de(), "x-account-id": "acct-1"}

Item = attention_pb2.AttentionItem


def _quando(segundos: int) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.FromSeconds(segundos)
    return ts


def item(
    id_: str,
    kind: int,
    *,
    prioridade: int,
    demanda: str = "",
    alvo: tuple[str, str] = ("thread", "th-1"),
    resolvido: bool = False,
) -> Item:
    """Um item como o núcleo o devolve — com a prioridade JÁ derivada por ele."""
    msg = Item(
        id=id_,
        account=common_pb2.AccountRef(id="acct-1"),
        kind=kind,
        target_kind=alvo[0],
        target_id=alvo[1],
        title=f"título de {id_}",
        summary=f"resumo de {id_}",
        priority=prioridade,
        opened_at=_quando(1_700_000_000),
    )
    # Ausente ≠ zerado: item de conta não pertence a demanda nenhuma, e um
    # DemandRef vazio faria a borda agrupar sob uma demanda de id "".
    if demanda:
        msg.demand.CopyFrom(common_pb2.DemandRef(id=demanda))
    if resolvido:
        msg.resolved_at.CopyFrom(_quando(1_700_003_600))
    return msg


# A fila como o núcleo a entrega: JÁ ORDENADA por ele, e de propósito numa
# ordem que nenhuma regra óbvia da borda reproduziria por acaso — a demanda
# dem-2 aparece antes de dem-1, e o item de conta (sem demanda) vem no meio.
# Se a borda reordenasse por prioridade, por demanda ou por id, quebraria.
FILA = [
    item("at-1", Item.KIND_MERGE_CONFLICT, prioridade=10_100, demanda="dem-2"),
    item("at-2", Item.KIND_INTEGRATION_BROKEN, prioridade=20_100,
         alvo=("resource", "res-1")),
    item("at-3", Item.KIND_GATE_PENDING, prioridade=40_050, demanda="dem-1",
         alvo=("stage", "spec")),
    item("at-4", Item.KIND_THREAD_BLOCKED, prioridade=70_010, demanda="dem-2"),
    item("at-5", Item.KIND_THREAD_BLOCKED, prioridade=70_120, demanda="dem-1"),
]


# ── duplos do núcleo ────────────────────────────────────────────────────────


class StreamFalso:
    """UMA assinatura de streaming no núcleo.

    Imita o objeto de chamada do `grpc.aio`: iterável e com `cancel()`. Guarda o
    pedido que recebeu e se foi cancelado — é a segunda parte que permite provar
    a coisa mais difícil de provar num stream: que o cliente indo embora mata a
    assinatura lá, em vez de deixá-la pendurada para sempre.
    """

    def __init__(self, itens, *, erro=None, segura=False, request=None, metadata=None):
        self.itens = list(itens)
        self.erro = erro
        # `segura` mantém a assinatura ABERTA depois do último item, como um
        # stream de verdade num período sem acontecimentos. Sem isso não dá para
        # testar desconexão: o stream acabaria sozinho antes.
        self.segura = asyncio.Event() if segura else None
        self.request = request
        self.metadata = dict(metadata or ())
        self.cancelada = False

    async def _itera(self):
        for i in self.itens:
            yield i
        if self.erro is not None:
            raise self.erro
        if self.segura is not None:
            await self.segura.wait()

    def __aiter__(self):
        return self._itera()

    def cancel(self):
        self.cancelada = True
        return True


class AtencaoFalsa:
    """Núcleo falso da caixa de atenção.

    `ListAttention` devolve a fila JÁ ordenada e um `open_total` MAIOR que o
    número de itens da página — é assim que se prova que o badge não é uma
    contagem da página disfarçada.

    `WatchAttention` abre uma assinatura NOVA por chamada, porque o teste de
    paridade assina duas vezes (uma por porta) e um duplo que devolvesse o mesmo
    stream esgotado faria a segunda porta parecer vazia.
    """

    # Aberto na conta inteira: sete, contra os cinco desta página.
    OPEN_TOTAL = 7

    def __init__(self, itens=FILA, atualizacoes=(), *, erro=None, segura=False):
        self.ListAttention = ChamadaFalsa(
            attention_pb2.ListAttentionResponse(
                items=itens, open_total=self.OPEN_TOTAL
            )
        )
        self.atualizacoes = list(atualizacoes)
        self.erro, self.segura = erro, segura
        self.streams: list[StreamFalso] = []

    def WatchAttention(self, request, *, metadata=None, **_):
        s = StreamFalso(
            self.atualizacoes,
            erro=self.erro,
            segura=self.segura,
            request=request,
            metadata=metadata,
        )
        self.streams.append(s)
        return s

    @property
    def ultimo_stream(self) -> StreamFalso:
        assert self.streams, "o stream não foi aberto"
        return self.streams[-1]


def aberto(it: Item) -> attention_pb2.AttentionUpdate:
    return attention_pb2.AttentionUpdate(
        change=attention_pb2.AttentionUpdate.CHANGE_OPENED, item=it
    )


def resolvido(kind: int, alvo: tuple[str, str]) -> attention_pb2.AttentionUpdate:
    """O aviso de fechamento como o núcleo o manda: PARCIAL, pelo alvo.

    Quem fecha conhece o alvo, não o id da projeção — então não vem id, nem
    título, nem `opened_at`, nem `resolved_at`. É o formato de verdade
    (internal/domain/attention/service.go), e imitar um item inteiro aqui
    esconderia que o cliente precisa casar pelo alvo.
    """
    return attention_pb2.AttentionUpdate(
        change=attention_pb2.AttentionUpdate.CHANGE_RESOLVED,
        item=Item(
            account=common_pb2.AccountRef(id="acct-1"),
            kind=kind,
            target_kind=alvo[0],
            target_id=alvo[1],
        ),
    )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def atencao(nucleo, monkeypatch):
    falso = AtencaoFalsa()
    monkeypatch.setattr(stubs, "attention_stub", lambda: falso)
    return falso


def _app_com_rotas():
    """O app real do BFF, com as rotas deste domínio registradas.

    `app/main.py` é do dono do repositório e ainda não inclui este router (ver o
    relatório). Montar aqui exercita o app de verdade — middlewares, decorators
    e tudo — sem disputar o arquivo com quem o mantém. O `if` deixa o teste
    continuar correto depois que o registro entrar no `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/attention" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def cliente(atencao):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub(atencao):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    Não reusa `servidor_grpc` do conftest porque `GrpcServer` ainda não registra
    este servicer (o registro é do dono do repositório). A pilha de
    interceptores é a MESMA, na mesma ordem — é ela que faz token, contexto e
    decorators valerem dentro do servicer, inclusive em streaming.
    """
    servidor = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_AttentionServiceServicer_to_server(AttentionServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.AttentionServiceStub(canal)
    finally:
        await servidor.stop(0)


def com_stream(monkeypatch, atualizacoes=(), **kwargs) -> AtencaoFalsa:
    falso = AtencaoFalsa(atualizacoes=atualizacoes, **kwargs)
    monkeypatch.setattr(stubs, "attention_stub", lambda: falso)
    return falso


# ── leitura do texto SSE ────────────────────────────────────────────────────


def quadros(texto: str) -> list[dict]:
    """Texto SSE → quadros. À mão de propósito: um parser de terceiros
    esconderia justamente o que se quer verificar (que o `id:` NÃO sai, que o
    `event:` sai com o nome certo)."""
    saida = []
    for bruto in texto.split("\r\n\r\n"):
        if not bruto.strip():
            continue
        quadro: dict = {"comment": []}
        for linha in bruto.split("\r\n"):
            if linha.startswith(": "):
                quadro["comment"].append(linha[2:])
            elif ": " in linha:
                chave, valor = linha.split(": ", 1)
                quadro[chave] = valor
        saida.append(quadro)
    return saida


def dados(quadro: dict) -> dict:
    return json.loads(quadro["data"])


def atencoes(texto: str) -> list[dict]:
    return [dados(q) for q in quadros(texto) if q.get("event") == rotas.ATENCAO]


# ── a fila ──────────────────────────────────────────────────────────────────


class TestOrdemEBadge:
    """As duas coisas que a borda NÃO decide: a ordem e o badge."""

    def test_a_ordem_e_a_do_nucleo(self, cliente):
        """A prioridade é derivada lá; uma segunda régua aqui furaria a fila.

        A fila do duplo é embaralhada de propósito em relação a demanda e id: se
        a borda ordenasse por qualquer critério próprio, a lista sairia diferente
        desta.
        """
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        assert [i["id"] for i in caixa["items"]] == [
            "at-1", "at-2", "at-3", "at-4", "at-5"
        ]
        assert [i["priority"] for i in caixa["items"]] == [
            10_100, 20_100, 40_050, 70_010, 70_120
        ]

    def test_a_prioridade_atravessa_sem_ser_recalculada(self, cliente, atencao):
        """Prioridade absurda continua absurda: a borda não conserta a régua.

        Se ela recalculasse (por idade, por tipo, por qualquer coisa), este
        número mudaria — e passariam a existir duas réguas para a mesma fila.
        """
        atencao.ListAttention.devolve(
            attention_pb2.ListAttentionResponse(
                items=[item("at-9", Item.KIND_PR_REVIEW, prioridade=999_999)],
                open_total=1,
            )
        )
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        assert caixa["items"][0]["priority"] == 999_999

    def test_o_badge_e_do_nucleo_e_nao_a_contagem_da_pagina(self, cliente):
        """Contar a página daria um badge que muda ao paginar."""
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        assert caixa["open_total"] == AtencaoFalsa.OPEN_TOTAL
        assert len(caixa["items"]) == 5
        assert caixa["open_total"] != len(caixa["items"])

    def test_filtrar_por_demanda_nao_mexe_no_badge(self, cliente, atencao):
        """`open_total` é da CONTA — é o número do sino, não o da tela aberta."""
        caixa = cliente.get(
            "/api/v1/attention?demand_id=dem-1", headers=CABECALHOS
        ).json()
        assert caixa["open_total"] == AtencaoFalsa.OPEN_TOTAL
        assert atencao.ListAttention.pedidos[0].demand.id == "dem-1"

    def test_sem_filtro_a_demanda_nao_e_preenchida(self, cliente, atencao):
        """Um DemandRef vazio seria uma demanda de id "" — filtro, não ausência
        de filtro."""
        cliente.get("/api/v1/attention", headers=CABECALHOS)
        assert not atencao.ListAttention.pedidos[0].HasField("demand")

    def test_resolvidos_so_por_pedido_explicito(self, cliente, atencao):
        cliente.get("/api/v1/attention", headers=CABECALHOS)
        assert atencao.ListAttention.pedidos[0].include_resolved is False
        cliente.get("/api/v1/attention?include_resolved=true", headers=CABECALHOS)
        assert atencao.ListAttention.pedidos[1].include_resolved is True

    def test_tamanho_de_pagina_atravessa(self, cliente, atencao):
        cliente.get("/api/v1/attention?page_size=20", headers=CABECALHOS)
        assert atencao.ListAttention.pedidos[0].page.size == 20


class TestAgrupamentoPorDemanda:
    """O que a borda ACRESCENTA — e o que ela não deixa de preservar."""

    def test_agrupa_por_demanda_preservando_a_ordem(self, cliente):
        """Os grupos saem na ordem do item mais urgente de cada um.

        Que é a ordem da primeira aparição na fila — nenhuma comparação nova. Um
        `sorted` daria o mesmo resultado hoje e passaria a divergir no dia em que
        a régua do núcleo mudasse.
        """
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        assert [g["demand_id"] for g in caixa["groups"]] == ["dem-2", "", "dem-1"]
        por_demanda = {g["demand_id"]: [i["id"] for i in g["items"]] for g in caixa["groups"]}
        assert por_demanda["dem-2"] == ["at-1", "at-4"]
        assert por_demanda["dem-1"] == ["at-3", "at-5"]

    def test_item_de_conta_e_um_grupo_e_nao_um_resto(self, cliente):
        """Integração quebrada para a conta inteira: é dos mais urgentes que
        existem, e jogá-lo num rodapé "outros" o esconderia justamente quando
        nada mais anda."""
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        conta = [g for g in caixa["groups"] if g["demand_id"] == ""][0]
        assert [i["id"] for i in conta["items"]] == ["at-2"]
        assert conta["items"][0]["target_kind"] == "resource"
        # E o item continua na fila plana também: agrupar não é mover.
        assert "at-2" in [i["id"] for i in caixa["items"]]

    def test_os_grupos_contem_os_mesmos_itens_da_fila(self, cliente):
        """Agrupar não pode perder nem duplicar item — seria uma fila mentindo
        sobre si mesma em dois lugares da mesma resposta."""
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        agrupados = [i["id"] for g in caixa["groups"] for i in g["items"]]
        assert sorted(agrupados) == sorted(i["id"] for i in caixa["items"])


class TestOQueOContratoNaoOferece:
    """A ausência é a decisão, e por isso é testada.

    A caixa é PROJEÇÃO: item nasce de evento e morre de evento, e quem fecha é o
    fato — o portão decidido, a thread destravada. Um "descartar" na borda
    tiraria o item da tela sem tirar o problema do mundo, e caixa que mente vira
    caixa ignorada (risco R-1 da spec).
    """

    @pytest.mark.parametrize(
        "metodo,caminho",
        [
            ("post", "/api/v1/attention/at-1/read"),
            ("post", "/api/v1/attention/at-1/dismiss"),
            ("delete", "/api/v1/attention/at-1"),
            ("patch", "/api/v1/attention/at-1"),
        ],
    )
    def test_nao_existe_marcar_como_lido_nem_descartar(self, cliente, metodo, caminho):
        r = getattr(cliente, metodo)(caminho, headers=CABECALHOS)
        assert r.status_code in (404, 405)

    def test_o_servico_grpc_so_tem_leitura(self):
        """O mesmo, no contrato: dois RPCs, os dois de leitura."""
        servico = bff.DESCRIPTOR.services_by_name["AttentionService"]
        assert {m.name for m in servico.methods} == {"ListAttention", "WatchAttention"}


class TestItemNaBorda:
    def test_alvo_e_par_e_nao_rota_pronta(self, cliente):
        """Guardar a rota pronta amarraria o backend ao desenho da tela."""
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        portao = [i for i in caixa["items"] if i["id"] == "at-3"][0]
        assert (portao["target_kind"], portao["target_id"]) == ("stage", "spec")
        assert portao["kind"] == "gate_pending"

    def test_item_aberto_nao_tem_data_de_resolucao(self, cliente):
        """None, não a época zero: 1970 numa fila ordenada por idade apareceria
        como o item mais antigo do mundo."""
        caixa = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        assert all(i["resolved_at"] is None for i in caixa["items"])
        assert all(i["opened_at"] is not None for i in caixa["items"])

    def test_item_resolvido_traz_a_data(self, cliente, atencao):
        """Item resolvido sai da caixa mas permanece na projeção: é dele que sai
        quanto tempo o dev levou para responder."""
        atencao.ListAttention.devolve(
            attention_pb2.ListAttentionResponse(
                items=[
                    item("at-8", Item.KIND_PR_REVIEW, prioridade=50_000, resolvido=True)
                ],
                open_total=0,
            )
        )
        caixa = cliente.get(
            "/api/v1/attention?include_resolved=true", headers=CABECALHOS
        ).json()
        assert caixa["items"][0]["resolved_at"] is not None


# ── o stream ────────────────────────────────────────────────────────────────


class TestSSE:
    def test_responde_text_event_stream(self, nucleo, monkeypatch):
        com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        # Sem buffer intermediário: um proxy que acumula transforma tempo real
        # em lote, e a caixa fica parada até a conexão fechar.
        assert r.headers["x-accel-buffering"] == "no"

    def test_abre_dizendo_de_quanto_em_quanto_reconectar(self, nucleo, monkeypatch):
        com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        assert quadros(r.text)[0]["retry"] == str(settings.sse_retry_ms)

    def test_nome_de_evento_proprio(self, nucleo, monkeypatch):
        """`attention` e não `event`: o corpo é uma MUDANÇA na fila, não um
        evento do log. São renderizadores diferentes na tela — a mesma razão
        pela qual `log` já é separado de `event`."""
        com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        assert [q.get("event") for q in quadros(r.text) if q.get("event")] == ["attention"]

    def test_abertura_traz_o_item_inteiro(self, nucleo, monkeypatch):
        com_stream(monkeypatch, [aberto(FILA[2])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        (upd,) = atencoes(r.text)
        assert upd["change"] == "opened"
        assert upd["item"]["id"] == "at-3"
        assert upd["item"]["kind"] == "gate_pending"
        assert upd["item"]["demand_id"] == "dem-1"

    def test_fechamento_identifica_pelo_alvo_e_a_borda_nao_inventa_id(
        self, nucleo, monkeypatch
    ):
        """O núcleo fecha pelo ALVO — quem fecha conhece o alvo, não o id.

        A borda repassa como está. Fabricar um id aqui, ou carimbar
        `resolved_at` com a hora do BFF, daria ao cockpit um dado que ninguém
        mediu: quem casa o aviso com o item na tela é o par (kind, alvo), e
        quem diz que ele fechou é o `change`.
        """
        com_stream(monkeypatch, [resolvido(Item.KIND_THREAD_BLOCKED, ("thread", "th-1"))])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        (upd,) = atencoes(r.text)
        assert upd["change"] == "resolved"
        assert upd["item"]["id"] == ""
        assert upd["item"]["resolved_at"] is None
        assert (upd["item"]["kind"], upd["item"]["target_id"]) == (
            "thread_blocked",
            "th-1",
        )

    def test_last_event_id_vira_o_cursor_do_nucleo(self, nucleo, monkeypatch):
        """A ligação que a ADR-0017 pede: cabeçalho do SSE → `since_event_id`."""
        falso = com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get(
                "/api/v1/stream/attention",
                headers={**CABECALHOS, "Last-Event-ID": "ev-40"},
            )
        assert falso.ultimo_stream.request.since_event_id == "ev-40"

    def test_cabecalho_vence_a_query(self, nucleo, monkeypatch):
        """A URL congela quando o EventSource é criado; o cabeçalho não.

        Preferir a query traria mudanças já vistas de volta a cada reconexão.
        """
        falso = com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get(
                "/api/v1/stream/attention?since_event_id=ev-7",
                headers={**CABECALHOS, "Last-Event-ID": "ev-40"},
            )
        assert falso.ultimo_stream.request.since_event_id == "ev-40"

    def test_query_serve_a_quem_nao_pode_mandar_cabecalho(self, nucleo, monkeypatch):
        falso = com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get("/api/v1/stream/attention?since_event_id=ev-7", headers=CABECALHOS)
        assert falso.ultimo_stream.request.since_event_id == "ev-7"

    def test_nao_emite_id_porque_o_nucleo_nao_manda_o_id_do_evento(
        self, nucleo, monkeypatch
    ):
        """`dop.v1.AttentionUpdate` não carrega o id do evento que a gerou.

        Sem ele não há `id:` HONESTO para emitir. Usar o id do ITEM seria pior
        que não emitir: ele não é posição no log, e voltaria ao núcleo como
        cursor sem significado na primeira reconexão. Este teste é o que impede
        alguém de "consertar" a retomada assim.
        """
        com_stream(monkeypatch, [aberto(FILA[0]), aberto(FILA[2])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        eventos = [q for q in quadros(r.text) if q.get("event") == rotas.ATENCAO]
        assert eventos and all("id" not in q for q in eventos)

    def test_conta_ativa_atravessa_nos_metadados(self, nucleo, monkeypatch):
        falso = com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get("/api/v1/stream/attention", headers=CABECALHOS)
        assert falso.ultimo_stream.metadata["x-account-id"] == "acct-1"


class TestErroDepoisDoPrimeiroByte:
    """200 já enviado, e aí o núcleo falha. Não existe trocar o status.

    Vale a mesma mecânica do fluxo de eventos, porque é literalmente o mesmo
    código (`routers/stream._eventos_sse`): o erro vira `event: error` em vez de
    um stream que morre em silêncio.
    """

    def test_consumidor_lento_vira_evento_de_erro_retentavel(self, nucleo, monkeypatch):
        com_stream(
            monkeypatch,
            [aberto(FILA[0])],
            erro=erro_grpc(grpc.StatusCode.UNAVAILABLE, "assinante lento demais"),
        )
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        assert r.status_code == 200
        corpo = dados([q for q in quadros(r.text) if q.get("event") == "error"][0])
        assert corpo["code"] == "UNAVAILABLE"
        assert corpo["retryable"] is True

    def test_detalhe_de_5xx_do_nucleo_nao_vaza(self, nucleo, monkeypatch):
        """Mesmo redator do resto da borda (`_detail_for`), não um segundo."""
        com_stream(
            monkeypatch,
            [aberto(FILA[0])],
            erro=erro_grpc(grpc.StatusCode.INTERNAL, "pq://user:senha@10.0.0.7 caiu"),
        )
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=CABECALHOS)
        corpo = dados([q for q in quadros(r.text) if q.get("event") == "error"][0])
        assert corpo["detail"] == "erro interno"
        assert "senha" not in r.text and "10.0.0.7" not in r.text


class TestAutorizacao:
    """Nem a caixa nem o stream podem ser a porta que nasce aberta."""

    def test_lista_sem_token_e_401(self, cliente):
        assert cliente.get("/api/v1/attention").status_code == 401

    def test_lista_sem_conta_ativa_e_400(self, cliente):
        r = cliente.get("/api/v1/attention", headers={"authorization": token_de()})
        assert r.status_code == 400

    def test_stream_sem_conta_ativa_e_400_e_nao_um_200_vazio(self, nucleo, monkeypatch):
        """A recusa acontece ANTES do primeiro byte: o caso de uso é chamado
        dentro do handler, não dentro do gerador."""
        falso = com_stream(monkeypatch, [aberto(FILA[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get(
                "/api/v1/stream/attention", headers={"authorization": token_de()}
            )
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/json")
        # E nenhuma assinatura foi aberta no núcleo.
        assert falso.streams == []


class TestDesconexao:
    """Cliente que some tem que matar a assinatura no núcleo.

    Stream que continua depois do cliente ir embora é vazamento de goroutine do
    outro lado da rede: o watcher fica no fan-out do núcleo para sempre.
    """

    async def test_abandonar_o_gerador_cancela_a_chamada(self, nucleo, monkeypatch):
        falso = com_stream(monkeypatch, [aberto(FILA[0])], segura=True)

        from app.platform.context import AuthContext, Principal, auth_ctx

        token = auth_ctx.set(
            AuthContext(principal=Principal(subject="s"), user_id="u-1", account_id="acct-1")
        )
        try:
            gerador = uc.watch_attention()
            assert (await anext(gerador)).item.id == "at-1"
            assert falso.ultimo_stream.cancelada is False
            # É isto que o EventSourceResponse faz ao ver `http.disconnect`.
            await gerador.aclose()
            assert falso.ultimo_stream.cancelada is True
        finally:
            auth_ctx.reset(token)


# ── gRPC ────────────────────────────────────────────────────────────────────


class TestGRPC:
    async def test_lista_com_grupos_e_badge(self, stub):
        resp = await stub.ListAttention(bff.ListAttentionRequest(), metadata=CONTA)
        assert [i.id for i in resp.items] == ["at-1", "at-2", "at-3", "at-4", "at-5"]
        assert [g.demand_id for g in resp.groups] == ["dem-2", "", "dem-1"]
        assert resp.open_total == AtencaoFalsa.OPEN_TOTAL

    async def test_item_aberto_nao_tem_campo_de_resolucao(self, stub):
        """Ausente ≠ zerado também na volta: um Timestamp zerado diria
        "resolvido em 1970", e o HasField do outro lado confirmaria."""
        resp = await stub.ListAttention(bff.ListAttentionRequest(), metadata=CONTA)
        assert resp.items[0].HasField("opened_at")
        assert not resp.items[0].HasField("resolved_at")

    async def test_watch_traz_abertura_e_fechamento(self, nucleo, monkeypatch, stub):
        com_stream(
            monkeypatch,
            [aberto(FILA[0]), resolvido(Item.KIND_MERGE_CONFLICT, ("pull_request", "pr-9"))],
        )
        recebidos = [
            u
            async for u in stub.WatchAttention(
                bff.WatchAttentionRequest(), metadata=CONTA
            )
        ]
        assert [u.change for u in recebidos] == [
            bff.ATTENTION_CHANGE_OPENED,
            bff.ATTENTION_CHANGE_RESOLVED,
        ]
        assert recebidos[0].item.kind == bff.ATTENTION_KIND_MERGE_CONFLICT
        # O fechamento identifica pelo alvo, e a borda não inventa o id.
        assert recebidos[1].item.id == ""
        assert recebidos[1].item.target_id == "pr-9"

    async def test_cursor_e_campo_porque_grpc_nao_tem_cabecalho(
        self, nucleo, monkeypatch, stub
    ):
        falso = com_stream(monkeypatch, [aberto(FILA[0])])
        async for _ in stub.WatchAttention(
            bff.WatchAttentionRequest(since_event_id="ev-40"), metadata=CONTA
        ):
            pass
        assert falso.ultimo_stream.request.since_event_id == "ev-40"

    async def test_sem_token_e_unauthenticated(self, stub, atencao):
        with pytest.raises(AioRpcError) as e:
            await stub.ListAttention(
                bff.ListAttentionRequest(), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert atencao.ListAttention.chamadas == []

    async def test_stream_sem_conta_ativa_e_invalid_argument(
        self, nucleo, monkeypatch, stub
    ):
        """Mesma regra do REST (SP-0), no status equivalente ao 400."""
        falso = com_stream(monkeypatch, [aberto(FILA[0])])
        with pytest.raises(AioRpcError) as e:
            async for _ in stub.WatchAttention(
                bff.WatchAttentionRequest(),
                metadata=metadados_de(token_de(), account_id=""),
            ):
                pass
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert falso.streams == []

    async def test_erro_do_nucleo_atravessa_com_o_status_dele(self, stub, atencao):
        atencao.ListAttention.falha_com(grpc.StatusCode.NOT_FOUND, "conta sumiu")
        with pytest.raises(AioRpcError) as e:
            await stub.ListAttention(bff.ListAttentionRequest(), metadata=CONTA)
        assert e.value.code() == grpc.StatusCode.NOT_FOUND

    async def test_detalhe_de_5xx_nao_vaza_nem_aqui(self, stub, atencao):
        atencao.ListAttention.falha_com(grpc.StatusCode.INTERNAL, "senha=hunter2")
        with pytest.raises(AioRpcError) as e:
            await stub.ListAttention(bff.ListAttentionRequest(), metadata=CONTA)
        assert e.value.details() == "erro interno"


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim.

    O alarme que dispara se alguém reimplementar o agrupamento (ou os nomes de
    tipo) num adaptador em vez de no caso de uso.
    """

    async def test_caixa_igual_nas_duas_portas(self, cliente, stub):
        rest = cliente.get("/api/v1/attention", headers=CABECALHOS).json()
        grpc_resp = await stub.ListAttention(bff.ListAttentionRequest(), metadata=CONTA)

        assert grpc_resp.open_total == rest["open_total"]
        assert [i.id for i in grpc_resp.items] == [i["id"] for i in rest["items"]]
        assert [i.priority for i in grpc_resp.items] == [
            i["priority"] for i in rest["items"]
        ]
        assert [g.demand_id for g in grpc_resp.groups] == [
            g["demand_id"] for g in rest["groups"]
        ]
        for g_grpc, g_rest in zip(grpc_resp.groups, rest["groups"], strict=True):
            assert [i.id for i in g_grpc.items] == [i["id"] for i in g_rest["items"]]
        # E o opcional, que é onde ausente ≠ zerado pode divergir entre pontas.
        for i_grpc, i_rest in zip(grpc_resp.items, rest["items"], strict=True):
            assert i_grpc.HasField("resolved_at") == (i_rest["resolved_at"] is not None)
            assert i_grpc.HasField("opened_at") == (i_rest["opened_at"] is not None)

    async def test_a_mesma_mudanca_sai_pelas_duas_portas(self, nucleo, monkeypatch, stub):
        atualizacoes = [
            aberto(FILA[0]),
            resolvido(Item.KIND_THREAD_BLOCKED, ("thread", "th-1")),
        ]
        com_stream(monkeypatch, atualizacoes)
        with TestClient(_app_com_rotas()) as c:
            do_sse = atencoes(
                c.get("/api/v1/stream/attention", headers=CABECALHOS).text
            )
        do_grpc = [
            u
            async for u in stub.WatchAttention(
                bff.WatchAttentionRequest(), metadata=CONTA
            )
        ]

        assert len(do_sse) == len(do_grpc) == 2
        nome_por_enum = {
            bff.ATTENTION_CHANGE_OPENED: "opened",
            bff.ATTENTION_CHANGE_RESOLVED: "resolved",
        }
        for sse, rpc in zip(do_sse, do_grpc, strict=True):
            assert sse["change"] == nome_por_enum[rpc.change]
            assert sse["item"]["id"] == rpc.item.id
            assert sse["item"]["target_kind"] == rpc.item.target_kind
            assert sse["item"]["target_id"] == rpc.item.target_id
            assert sse["item"]["priority"] == rpc.item.priority
            # O nome do tipo na borda REST e o enum no gRPC são o mesmo fato.
            assert rpc.item.kind == Item.Kind.Value(
                "KIND_" + sse["item"]["kind"].upper()
            )
