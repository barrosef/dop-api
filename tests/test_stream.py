"""Streaming nas duas portas: SSE para o browser, gRPC para CLI e agentes.

Os duplos e as fixtures vivem AQUI, e não no `conftest.py`, de propósito: o
conftest é território compartilhado e há trabalho acontecendo em paralelo nele.
O que este arquivo precisa do conftest é só o que já existe lá (o núcleo falso
de identidade, o token e os metadados) — nada é acrescentado do outro lado.

O núcleo NUNCA sobe: o duplo é uma chamada de server-streaming falsa que
registra o pedido recebido e se foi cancelada. É essa segunda parte que permite
provar a coisa mais difícil de provar num stream — que o cliente indo embora
mata a assinatura lá no núcleo, em vez de deixá-la pendurada para sempre.
"""

import asyncio
import json

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import struct_pb2, timestamp_pb2
from grpc.aio import AioRpcError

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, demand_pb2, event_pb2, execution_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import stream_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import stream_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.grpcapi.stream import StreamServicer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import stream as rotas
from app.settings import settings
from app.usecases import stream as uc
from tests.conftest import PROJECT, erro_grpc, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS = {"authorization": token_de(), "x-account-id": "acct-1"}


# ── duplos do núcleo ────────────────────────────────────────────────────────


def _struct(dados: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(dados)
    return s


def _quando(segundos: int) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.FromSeconds(segundos)
    return ts


def envelope(id_: str, tipo="dop.demand.stage.advanced", **payload) -> event_pb2.EventEnvelope:
    return event_pb2.EventEnvelope(
        id=id_,
        account=common_pb2.AccountRef(id="acct-1"),
        aggregate="demand",
        aggregate_id="dem-1",
        type=tipo,
        payload=_struct(payload or {"stage": "build"}),
        occurred_at=_quando(1_700_000_000),
    )


class StreamFalso:
    """UMA assinatura de streaming no núcleo.

    Imita o objeto de chamada do `grpc.aio`: é iterável e tem `cancel()`. O que
    ele guarda — o pedido que recebeu e se foi cancelado — é exatamente o que os
    testes precisam olhar.
    """

    def __init__(self, itens, *, erro=None, segura=False, pausa=0.0, request=None, metadata=None):
        self.itens = list(itens)
        self.erro = erro
        # Intervalo entre itens — um stream de verdade não entrega tudo de uma
        # vez, e é no silêncio entre eventos que o heartbeat precisa aparecer.
        self.pausa = pausa
        # `segura` mantém a assinatura ABERTA depois do último item, como um
        # stream de verdade num período sem acontecimentos. Sem isso não dá para
        # testar desconexão: o stream acabaria sozinho antes.
        self.segura = asyncio.Event() if segura else None
        self.request = request
        self.metadata = dict(metadata or ())
        self.cancelada = False

    async def _itera(self):
        for item in self.itens:
            if self.pausa:
                await asyncio.sleep(self.pausa)
            yield item
        if self.erro is not None:
            raise self.erro
        if self.segura is not None:
            await self.segura.wait()

    def __aiter__(self):
        return self._itera()

    def cancel(self):
        self.cancelada = True
        return True


class ServicoFalso:
    """Serviço de streaming do núcleo: uma assinatura NOVA por chamada.

    Nova a cada chamada porque o teste de paridade assina duas vezes (uma por
    porta) e um duplo que devolvesse o mesmo stream esgotado faria a segunda
    porta parecer vazia.
    """

    def __init__(self, rpc: str, itens=(), *, erro=None, segura=False, pausa=0.0):
        self.itens, self.erro, self.segura, self.pausa = list(itens), erro, segura, pausa
        self.chamadas: list[StreamFalso] = []
        setattr(self, rpc, self._abrir)

    def _abrir(self, request, *, metadata=None, **_):
        s = StreamFalso(
            self.itens,
            erro=self.erro,
            segura=self.segura,
            pausa=self.pausa,
            request=request,
            metadata=metadata,
        )
        self.chamadas.append(s)
        return s

    @property
    def ultima(self) -> StreamFalso:
        assert self.chamadas, "o stream não foi aberto"
        return self.chamadas[-1]


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def eventos(nucleo, monkeypatch):
    """EventService falso, com dois eventos e a assinatura encerrando sozinha."""
    falso = ServicoFalso("WatchEvents", [envelope("ev-1"), envelope("ev-2")])
    monkeypatch.setattr(stubs, "event_stub", lambda: falso)
    return falso


@pytest.fixture
def demanda(nucleo, monkeypatch):
    falso = ServicoFalso(
        "WatchDemand",
        [
            demand_pb2.DemandEvent(
                type="message.posted",
                aggregate="demand",
                payload=_struct({"text": "oi"}),
                at=_quando(1_700_000_000),
            )
        ],
    )
    monkeypatch.setattr(stubs, "demand_stub", lambda: falso)
    return falso


@pytest.fixture
def execucao(nucleo, monkeypatch):
    falso = ServicoFalso(
        "StreamLogs",
        [
            execution_pb2.LogLine(
                source="app", service="api", line="subiu", at=_quando(1_700_000_000)
            )
        ],
    )
    monkeypatch.setattr(stubs, "execution_stub", lambda: falso)
    return falso


def _app():
    """A aplicação com o router de stream montado.

    O registro definitivo é no `app/main.py`, que não é deste agente. Montar
    aqui prova o router pelo caminho real (middlewares, decorators e tudo) sem
    disputar o arquivo com quem o mantém.
    """
    app = create_app()
    app.include_router(rotas.router)
    return app


@pytest.fixture
def cliente_sse(eventos):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def cliente_demanda(demanda):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def cliente_log(execucao):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
async def servidor_stream(nucleo):
    """Servidor gRPC de verdade, com o StreamServicer e os MESMOS interceptores.

    Não usa o `GrpcServer` porque o registro do StreamService lá é de quem
    mantém `app/grpcapi/server.py`. A pilha de interceptores é copiada na mesma
    ordem — é ela que este arquivo precisa exercitar, já que foi ela que ganhou
    o suporte a streaming.
    """
    servidor = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_StreamServiceServicer_to_server(StreamServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        yield porta
    finally:
        await servidor.stop(grace=0)


@pytest.fixture
async def stub_stream(servidor_stream):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor_stream}") as canal:
        yield bff_grpc.StreamServiceStub(canal)


# ── leitura do texto SSE ────────────────────────────────────────────────────


def quadros(texto: str) -> list[dict]:
    """Texto SSE → lista de quadros com os campos que interessam.

    Escrito à mão de propósito: um parser de terceiros esconderia justamente o
    que se quer verificar (que o `id:` sai, que o `event:` sai com o nome certo).
    """
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


# ── SSE ─────────────────────────────────────────────────────────────────────


class TestSSE:
    def test_responde_text_event_stream(self, cliente_sse):
        r = cliente_sse.get("/api/v1/stream/events", headers=CABECALHOS)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        # Sem buffer intermediário: um proxy que acumula transforma tempo real
        # em lote, e o cockpit fica parado até a conexão fechar.
        assert r.headers["x-accel-buffering"] == "no"

    def test_abre_dizendo_de_quanto_em_quanto_reconectar(self, cliente_sse):
        r = cliente_sse.get("/api/v1/stream/events", headers=CABECALHOS)
        assert quadros(r.text)[0]["retry"] == str(settings.sse_retry_ms)

    def test_id_do_sse_e_o_id_do_evento_do_nucleo(self, cliente_sse):
        """O `id:` é o que faz a retomada funcionar — e ele não é nosso.

        Um contador local seria um id que o núcleo não sabe traduzir em posição
        no log: na reconexão, `since_event_id` chegaria lá sem significado.
        """
        r = cliente_sse.get("/api/v1/stream/events", headers=CABECALHOS)
        eventos_sse = [q for q in quadros(r.text) if q.get("event") == "event"]
        assert [q["id"] for q in eventos_sse] == ["ev-1", "ev-2"]
        assert dados(eventos_sse[0])["type"] == "dop.demand.stage.advanced"
        assert dados(eventos_sse[0])["payload"] == {"stage": "build"}

    def test_last_event_id_vira_o_cursor_do_nucleo(self, cliente_sse, eventos):
        """A ligação que a ADR pede: cabeçalho do SSE → `since_event_id` do core."""
        cliente_sse.get(
            "/api/v1/stream/events", headers={**CABECALHOS, "Last-Event-ID": "ev-40"}
        )
        assert eventos.ultima.request.since_event_id == "ev-40"

    def test_query_serve_a_quem_nao_pode_mandar_cabecalho(self, cliente_sse, eventos):
        """EventSource não deixa definir cabeçalho na PRIMEIRA conexão."""
        cliente_sse.get(
            "/api/v1/stream/events?since_event_id=ev-7", headers=CABECALHOS
        )
        assert eventos.ultima.request.since_event_id == "ev-7"

    def test_cabecalho_vence_a_query(self, cliente_sse, eventos):
        """A URL congela no momento em que o EventSource é criado; o cabeçalho não.

        Preferir a query traria eventos já vistos de volta a cada reconexão.
        """
        cliente_sse.get(
            "/api/v1/stream/events?since_event_id=ev-7",
            headers={**CABECALHOS, "Last-Event-ID": "ev-40"},
        )
        assert eventos.ultima.request.since_event_id == "ev-40"

    def test_filtros_atravessam_para_o_nucleo(self, cliente_sse, eventos):
        cliente_sse.get(
            "/api/v1/stream/events?aggregate=demand&types=dop.demand.stage.advanced",
            headers=CABECALHOS,
        )
        pedido = eventos.ultima.request
        assert list(pedido.aggregate) == ["demand"]
        assert list(pedido.types) == ["dop.demand.stage.advanced"]

    def test_conta_ativa_atravessa_nos_metadados(self, cliente_sse, eventos):
        cliente_sse.get("/api/v1/stream/events", headers=CABECALHOS)
        assert eventos.ultima.metadata is not None
        assert dict(eventos.ultima.metadata)["x-account-id"] == "acct-1"

    def test_heartbeat_sai_como_comentario(self, nucleo, monkeypatch):
        """`: ping` é comentário SSE: o browser ignora, o proxy vê tráfego.

        O duplo espera entre um evento e outro justamente para criar o silêncio
        em que o proxy derrubaria a conexão — é ali que o ping tem que aparecer.
        """
        monkeypatch.setattr(settings, "sse_ping_s", 0.01)
        monkeypatch.setattr(
            stubs,
            "event_stub",
            lambda: ServicoFalso(
                "WatchEvents", [envelope("ev-1"), envelope("ev-2")], pausa=0.05
            ),
        )
        with TestClient(_app()) as cliente:
            r = cliente.get("/api/v1/stream/events", headers=CABECALHOS)
        assert any(q["comment"] and q["comment"][0].startswith("ping") for q in quadros(r.text))


class TestErroDepoisDoPrimeiroByte:
    """A armadilha clássica: 200 já enviado, e aí o núcleo falha.

    Não existe trocar o status depois disso. Ou o erro vira um evento que o
    cliente sabe ler, ou vira um stream que morre em silêncio — e um cockpit que
    mostra dados velhos achando que está ao vivo.
    """

    @pytest.fixture
    def cliente_com_falha(self, nucleo, monkeypatch, request):
        code, detalhe = request.param
        falso = ServicoFalso(
            "WatchEvents", [envelope("ev-1")], erro=erro_grpc(code, detalhe)
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: falso)
        with TestClient(_app()) as c:
            yield c

    @pytest.mark.parametrize(
        "cliente_com_falha",
        [(grpc.StatusCode.UNAVAILABLE, "assinante lento demais: reconecte com since_event_id")],
        indirect=True,
    )
    def test_consumidor_lento_vira_evento_de_erro_retentavel(self, cliente_com_falha):
        r = cliente_com_falha.get("/api/v1/stream/events", headers=CABECALHOS)
        # 200: o status foi decidido antes do primeiro evento e não muda mais.
        assert r.status_code == 200
        erro = [q for q in quadros(r.text) if q.get("event") == "error"][0]
        corpo = dados(erro)
        assert corpo["code"] == "UNAVAILABLE"
        assert corpo["retryable"] is True
        # O cursor volta no corpo: quem reconecta por `fetch` (ou depois de um
        # F5) não tem o Last-Event-ID automático do EventSource.
        assert corpo["since_event_id"] == "ev-1"
        # O texto é NOSSO, não o do núcleo: UNAVAILABLE é 503, e o `_detail_for`
        # não deixa detalhe de 5xx do núcleo sair. Quem carrega a instrução é o
        # campo estruturado, que é de máquina.
        assert corpo["detail"] == rotas._RECONNECT
        assert "assinante lento" not in r.text

    @pytest.mark.parametrize(
        "cliente_com_falha",
        [(grpc.StatusCode.INTERNAL, "pq://user:senha@10.0.0.7/dop falhou")],
        indirect=True,
    )
    def test_detalhe_de_5xx_do_nucleo_nao_vaza(self, cliente_com_falha):
        """Mesmo redator do resto da borda (`_detail_for`), não um segundo."""
        r = cliente_com_falha.get("/api/v1/stream/events", headers=CABECALHOS)
        corpo = dados([q for q in quadros(r.text) if q.get("event") == "error"][0])
        assert corpo["detail"] == "internal error"
        assert corpo["retryable"] is False
        assert "senha" not in r.text and "10.0.0.7" not in r.text

    @pytest.mark.parametrize(
        "cliente_com_falha", [(grpc.StatusCode.NOT_FOUND, "demanda não existe")], indirect=True
    )
    def test_o_que_ja_saiu_continua_valendo(self, cliente_com_falha):
        """O erro não invalida os eventos entregues antes dele."""
        r = cliente_com_falha.get("/api/v1/stream/events", headers=CABECALHOS)
        nomes = [q.get("event") for q in quadros(r.text) if q.get("event")]
        assert nomes == ["event", "error"]


class TestAutorizacao:
    """Stream não pode ser a porta que nasce aberta.

    Estas recusas acontecem ANTES do primeiro byte — o caso de uso é chamado
    dentro do handler, não dentro do gerador — e por isso são status HTTP de
    verdade, com corpo JSON, e não um 200 que morre no primeiro quadro.
    """

    def test_sem_token_e_401(self, cliente_sse):
        r = cliente_sse.get("/api/v1/stream/events")
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/json")

    def test_sem_conta_ativa_e_400(self, cliente_sse):
        r = cliente_sse.get(
            "/api/v1/stream/events", headers={"authorization": token_de()}
        )
        assert r.status_code == 400

    def test_recusa_nao_abre_assinatura_no_nucleo(self, cliente_sse, eventos):
        cliente_sse.get("/api/v1/stream/events", headers={"authorization": token_de()})
        assert eventos.chamadas == []


class TestDesconexao:
    """Cliente que some tem que matar a assinatura no núcleo.

    Stream que continua depois do cliente ir embora é vazamento de goroutine do
    outro lado da rede: o watcher fica no fan-out do núcleo para sempre.
    """

    async def test_abandonar_o_gerador_cancela_a_chamada(self, nucleo, monkeypatch):
        falso = ServicoFalso("WatchEvents", [envelope("ev-1")], segura=True)
        monkeypatch.setattr(stubs, "event_stub", lambda: falso)

        from app.platform.context import AuthContext, Principal, auth_ctx

        token = auth_ctx.set(
            AuthContext(principal=Principal(subject="s"), user_id="u-1", account_id="acct-1")
        )
        try:
            gerador = uc.watch_account_events()
            assert (await anext(gerador)).id == "ev-1"
            assert falso.ultima.cancelada is False
            # É isto que o EventSourceResponse faz quando vê `http.disconnect`.
            await gerador.aclose()
            assert falso.ultima.cancelada is True
        finally:
            auth_ctx.reset(token)

    async def test_cliente_grpc_que_cancela_encerra_a_assinatura(
        self, stub_stream, nucleo, monkeypatch
    ):
        """O mesmo, ponta a ponta: o cancelamento vem do FIO, não do teste."""
        falso = ServicoFalso("WatchEvents", [envelope("ev-1")], segura=True)
        monkeypatch.setattr(stubs, "event_stub", lambda: falso)

        chamada = stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=CONTA)
        assert (await chamada.read()).id == "ev-1"
        chamada.cancel()

        for _ in range(100):
            if falso.chamadas and falso.ultima.cancelada:
                break
            await asyncio.sleep(0.01)
        assert falso.ultima.cancelada is True


# ── gRPC ────────────────────────────────────────────────────────────────────


class TestGRPC:
    async def test_watch_events(self, stub_stream, eventos):
        recebidos = [e async for e in stub_stream.WatchEvents(
            bff.WatchEventsRequest(), metadata=CONTA
        )]
        assert [e.id for e in recebidos] == ["ev-1", "ev-2"]
        assert recebidos[0].payload["stage"] == "build"

    async def test_cursor_e_campo_porque_grpc_nao_tem_cabecalho(self, stub_stream, eventos):
        async for _ in stub_stream.WatchEvents(
            bff.WatchEventsRequest(since_event_id="ev-40"), metadata=CONTA
        ):
            pass
        assert eventos.ultima.request.since_event_id == "ev-40"

    async def test_sem_token_e_unauthenticated(self, stub_stream, eventos):
        """O interceptor de auth passou a valer em streaming — antes não valia."""
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(
                bff.WatchEventsRequest(), metadata=metadados_de(None)
            ):
                pass
        assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert eventos.chamadas == []

    async def test_sem_conta_ativa_e_invalid_argument(self, stub_stream, eventos):
        """Mesma regra do REST (SP-0), traduzida para o status equivalente ao 400."""
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(
                bff.WatchEventsRequest(), metadata=metadados_de(token_de(), account_id="")
            ):
                pass
        assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    async def test_erro_depois_do_primeiro_item_vira_status(self, stub_stream, nucleo, monkeypatch):
        """Aqui o status ainda cabe: em gRPC ele viaja nos trailers.

        É a diferença de transporte que justifica o SSE precisar de um evento
        `error` e o gRPC não.
        """
        falso = ServicoFalso(
            "WatchEvents",
            [envelope("ev-1")],
            erro=erro_grpc(grpc.StatusCode.UNAVAILABLE, "assinante lento demais"),
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: falso)

        chamada = stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=CONTA)
        assert (await chamada.read()).id == "ev-1"
        with pytest.raises(AioRpcError) as exc:
            await chamada.read()
        # O código é o sinal, e ele chega inteiro depois de um item já entregue.
        # O detalhe some pelo mesmo motivo do SSE: 503 do núcleo não se repete.
        assert exc.value.code() == grpc.StatusCode.UNAVAILABLE
        assert exc.value.details() == "internal error"

    async def test_detalhe_de_5xx_nao_vaza_nem_aqui(self, stub_stream, nucleo, monkeypatch):
        falso = ServicoFalso(
            "WatchEvents", erro=erro_grpc(grpc.StatusCode.INTERNAL, "senha=hunter2")
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: falso)
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=CONTA):
                pass
        assert exc.value.details() == "internal error"

    async def test_tail_logs(self, stub_stream, execucao):
        linhas = [
            ll
            async for ll in stub_stream.TailLogs(
                bff.TailLogsRequest(sandbox_id="sbx-1", source="app"), metadata=CONTA
            )
        ]
        assert [ll.line for ll in linhas] == ["subiu"]
        assert execucao.ultima.request.sandbox_id == "sbx-1"
        assert execucao.ultima.request.source == "app"


class TestDemandaSemCursor:
    """`WatchDemand` não tem `since_event_id` no núcleo — e a borda não finge que tem."""

    def test_sse_da_demanda_nao_emite_id(self, cliente_demanda):
        r = cliente_demanda.get("/api/v1/stream/demands/dem-1", headers=CABECALHOS)
        eventos_sse = [q for q in quadros(r.text) if q.get("event") == "event"]
        assert eventos_sse and all("id" not in q for q in eventos_sse)

    def test_a_demanda_vira_o_aggregate_id(self, cliente_demanda):
        r = cliente_demanda.get("/api/v1/stream/demands/dem-1", headers=CABECALHOS)
        corpo = dados([q for q in quadros(r.text) if q.get("event") == "event"][0])
        assert corpo["aggregate_id"] == "dem-1"
        assert corpo["id"] == ""

    def test_log_sai_com_nome_de_evento_proprio(self, cliente_log):
        """`log` e `event` são coisas diferentes na tela; separar é do protocolo."""
        r = cliente_log.get("/api/v1/stream/sandboxes/sbx-1/logs", headers=CABECALHOS)
        quadro = [q for q in quadros(r.text) if q.get("event") == "log"][0]
        assert dados(quadro)["line"] == "subiu"


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim.

    O alarme que dispara se alguém reimplementar a tradução no servicer ou no
    router em vez de no caso de uso.
    """

    async def test_o_mesmo_evento_sai_pelas_duas_portas(self, eventos, stub_stream):
        with TestClient(_app()) as cliente:
            rest = cliente.get("/api/v1/stream/events", headers=CABECALHOS)
        do_sse = [dados(q) for q in quadros(rest.text) if q.get("event") == "event"]
        do_grpc = [
            e
            async for e in stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=CONTA)
        ]

        assert len(do_sse) == len(do_grpc) == 2
        for sse, rpc in zip(do_sse, do_grpc, strict=True):
            assert sse["id"] == rpc.id
            assert sse["type"] == rpc.type
            assert sse["aggregate"] == rpc.aggregate
            assert sse["aggregate_id"] == rpc.aggregate_id
            assert sse["payload"] == dict(rpc.payload)
            # O horário é o mesmo instante nas duas roupas: ISO-8601 no JSON,
            # Timestamp no protobuf.
            assert sse["occurred_at"].startswith("2023-11-14")
            assert rpc.occurred_at.seconds == 1_700_000_000
