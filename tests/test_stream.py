"""Streaming nas duas portas: SSE para o browser, gRPC para CLI e agentes.

Os doubles e as fixtures vivem AQUI, e não no `conftest.py`, de propósito: o
conftest é território compartilhado e há trabalho acontecendo em paralelo nele.
O que este arquivo precisa do conftest é só o que já existe lá (o núcleo fake
de identidade, o token e os metadata) — nada é acrescentado do outro lado.

O núcleo NUNCA sobe: o double é uma call de server-streaming falsa que
registra o request recebido e se foi cancelada. É essa segunda parte que permite
provar a coisa mais difícil de provar num stream — que o client indo embora
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
from tests.conftest import PROJECT, grpc_error, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}


# ── doubles do núcleo ────────────────────────────────────────────────────────


def _struct(data: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(data)
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

    Imita o objeto de call do `grpc.aio`: é iterável e tem `cancel()`. O que
    ele guarda — o request que recebeu e se foi cancelado — é exatamente o que os
    testes precisam olhar.
    """

    def __init__(self, items, *, err=None, segura=False, pausa=0.0, request=None, metadata=None):
        self.items = list(items)
        self.err = err
        # Intervalo entre items — um stream de verdade não entrega tudo de uma
        # vez, e é no silêncio entre eventos que o heartbeat precisa aparecer.
        self.pausa = pausa
        # `segura` mantém a assinatura ABERTA depois do último item, como um
        # stream de verdade num período sem acontecimentos. Sem isso não dá para
        # testar desconexão: o stream acabaria sozinho antes.
        self.segura = asyncio.Event() if segura else None
        self.request = request
        self.metadata = dict(metadata or ())
        self.cancelada = False

    async def _iterate(self):
        for item in self.items:
            if self.pausa:
                await asyncio.sleep(self.pausa)
            yield item
        if self.err is not None:
            raise self.err
        if self.segura is not None:
            await self.segura.wait()

    def __aiter__(self):
        return self._iterate()

    def cancel(self):
        self.cancelada = True
        return True


class ServicoFalso:
    """Serviço de streaming do núcleo: uma assinatura NOVA por call.

    Nova a cada call porque o teste de paridade assina duas vezes (uma por
    porta) e um double que devolvesse o mesmo stream esgotado faria a segunda
    porta parecer vazia.
    """

    def __init__(self, rpc: str, items=(), *, err=None, segura=False, pausa=0.0):
        self.items, self.err, self.segura, self.pausa = list(items), err, segura, pausa
        self.calls: list[StreamFalso] = []
        setattr(self, rpc, self._abrir)

    def _abrir(self, request, *, metadata=None, **_):
        s = StreamFalso(
            self.items,
            err=self.err,
            segura=self.segura,
            pausa=self.pausa,
            request=request,
            metadata=metadata,
        )
        self.calls.append(s)
        return s

    @property
    def last(self) -> StreamFalso:
        assert self.calls, "o stream não foi aberto"
        return self.calls[-1]


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def eventos(core, monkeypatch):
    """EventService fake, com dois eventos e a assinatura encerrando sozinha."""
    fake = ServicoFalso("WatchEvents", [envelope("ev-1"), envelope("ev-2")])
    monkeypatch.setattr(stubs, "event_stub", lambda: fake)
    return fake


@pytest.fixture
def demand(core, monkeypatch):
    fake = ServicoFalso(
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
    monkeypatch.setattr(stubs, "demand_stub", lambda: fake)
    return fake


@pytest.fixture
def execucao(core, monkeypatch):
    fake = ServicoFalso(
        "StreamLogs",
        [
            execution_pb2.LogLine(
                source="app", service="api", line="subiu", at=_quando(1_700_000_000)
            )
        ],
    )
    monkeypatch.setattr(stubs, "execution_stub", lambda: fake)
    return fake


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
def client_sse(eventos):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def client_demand(demand):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def cliente_log(execucao):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
async def servidor_stream(core):
    """Servidor gRPC de verdade, com o StreamServicer e os MESMOS interceptores.

    Não usa o `GrpcServer` porque o registro do StreamService lá é de quem
    mantém `app/grpcapi/server.py`. A pilha de interceptores é copiada na mesma
    ordem — é ela que este arquivo precisa exercitar, já que foi ela que ganhou
    o suporte a streaming.
    """
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_StreamServiceServicer_to_server(StreamServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield porta
    finally:
        await server.stop(grace=0)


@pytest.fixture
async def stub_stream(servidor_stream):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor_stream}") as channel:
        yield bff_grpc.StreamServiceStub(channel)


# ── leitura do text SSE ────────────────────────────────────────────────────


def frames(text: str) -> list[dict]:
    """Texto SSE → lista de frames com os campos que interessam.

    Escrito à mão de propósito: um parser de terceiros esconderia justamente o
    que se quer verificar (que o `id:` sai, que o `event:` sai com o name certo).
    """
    saida = []
    for bruto in text.split("\r\n\r\n"):
        if not bruto.strip():
            continue
        board: dict = {"comment": []}
        for linha in bruto.split("\r\n"):
            if linha.startswith(": "):
                board["comment"].append(linha[2:])
            elif ": " in linha:
                key, value = linha.split(": ", 1)
                board[key] = value
        saida.append(board)
    return saida


def data(board: dict) -> dict:
    return json.loads(board["data"])


# ── SSE ─────────────────────────────────────────────────────────────────────


class TestSSE:
    def test_responde_text_event_stream(self, client_sse):
        r = client_sse.get("/api/v1/stream/events", headers=HEADERS)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        # Sem buffer intermediário: um proxy que acumula transforma tempo real
        # em lote, e o cockpit fica parado até a conexão fechar.
        assert r.headers["x-accel-buffering"] == "no"

    def test_abre_dizendo_de_quanto_em_quanto_reconectar(self, client_sse):
        r = client_sse.get("/api/v1/stream/events", headers=HEADERS)
        assert frames(r.text)[0]["retry"] == str(settings.sse_retry_ms)

    def test_id_do_sse_e_o_id_do_evento_do_nucleo(self, client_sse):
        """O `id:` é o que faz a retomada funcionar — e ele não é nosso.

        Um contador local seria um id que o núcleo não sabe traduzir em posição
        no log: na reconexão, `since_event_id` chegaria lá sem significado.
        """
        r = client_sse.get("/api/v1/stream/events", headers=HEADERS)
        eventos_sse = [q for q in frames(r.text) if q.get("event") == "event"]
        assert [q["id"] for q in eventos_sse] == ["ev-1", "ev-2"]
        assert data(eventos_sse[0])["type"] == "dop.demand.stage.advanced"
        assert data(eventos_sse[0])["payload"] == {"stage": "build"}

    def test_last_event_id_vira_o_cursor_do_nucleo(self, client_sse, eventos):
        """A ligação que a ADR pede: cabeçalho do SSE → `since_event_id` do core."""
        client_sse.get(
            "/api/v1/stream/events", headers={**HEADERS, "Last-Event-ID": "ev-40"}
        )
        assert eventos.last.request.since_event_id == "ev-40"

    def test_query_serve_a_quem_nao_pode_mandar_cabecalho(self, client_sse, eventos):
        """EventSource não deixa definir cabeçalho na PRIMEIRA conexão."""
        client_sse.get(
            "/api/v1/stream/events?since_event_id=ev-7", headers=HEADERS
        )
        assert eventos.last.request.since_event_id == "ev-7"

    def test_cabecalho_vence_a_query(self, client_sse, eventos):
        """A URL congela no momento em que o EventSource é criado; o cabeçalho não.

        Preferir a query traria eventos já vistos de volta a cada reconexão.
        """
        client_sse.get(
            "/api/v1/stream/events?since_event_id=ev-7",
            headers={**HEADERS, "Last-Event-ID": "ev-40"},
        )
        assert eventos.last.request.since_event_id == "ev-40"

    def test_filtros_atravessam_para_o_nucleo(self, client_sse, eventos):
        client_sse.get(
            "/api/v1/stream/events?aggregate=demand&types=dop.demand.stage.advanced",
            headers=HEADERS,
        )
        request = eventos.last.request
        assert list(request.aggregate) == ["demand"]
        assert list(request.types) == ["dop.demand.stage.advanced"]

    def test_conta_ativa_atravessa_nos_metadados(self, client_sse, eventos):
        client_sse.get("/api/v1/stream/events", headers=HEADERS)
        assert eventos.last.metadata is not None
        assert dict(eventos.last.metadata)["x-account-id"] == "acct-1"

    def test_heartbeat_sai_como_comentario(self, core, monkeypatch):
        """`: ping` é comentário SSE: o browser ignora, o proxy vê tráfego.

        O double espera entre um evento e outro justamente para criar o silêncio
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
        with TestClient(_app()) as client:
            r = client.get("/api/v1/stream/events", headers=HEADERS)
        assert any(q["comment"] and q["comment"][0].startswith("ping") for q in frames(r.text))


class TestErroDepoisDoPrimeiroByte:
    """A armadilha clássica: 200 já enviado, e aí o núcleo falha.

    Não existe trocar o status depois disso. Ou o err vira um evento que o
    client sabe ler, ou vira um stream que morre em silêncio — e um cockpit que
    mostra data velhos achando que está ao vivo.
    """

    @pytest.fixture
    def failing_client(self, core, monkeypatch, request):
        code, detalhe = request.param
        fake = ServicoFalso(
            "WatchEvents", [envelope("ev-1")], err=grpc_error(code, detalhe)
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)
        with TestClient(_app()) as c:
            yield c

    @pytest.mark.parametrize(
        "failing_client",
        [(grpc.StatusCode.UNAVAILABLE, "assinante lento demais: reconecte com since_event_id")],
        indirect=True,
    )
    def test_consumidor_lento_vira_evento_de_erro_retentavel(self, failing_client):
        r = failing_client.get("/api/v1/stream/events", headers=HEADERS)
        # 200: o status foi decidido antes do primeiro evento e não muda mais.
        assert r.status_code == 200
        err = [q for q in frames(r.text) if q.get("event") == "error"][0]
        body = data(err)
        assert body["code"] == "UNAVAILABLE"
        assert body["retryable"] is True
        # O cursor volta no body: quem reconecta por `fetch` (ou depois de um
        # F5) não tem o Last-Event-ID automático do EventSource.
        assert body["since_event_id"] == "ev-1"
        # O text é NOSSO, não o do núcleo: UNAVAILABLE é 503, e o `_detail_for`
        # não deixa detalhe de 5xx do núcleo sair. Quem carrega a instrução é o
        # campo estruturado, que é de máquina.
        assert body["detail"] == rotas._RECONNECT
        assert "assinante lento" not in r.text

    @pytest.mark.parametrize(
        "failing_client",
        [(grpc.StatusCode.INTERNAL, "pq://user:senha@10.0.0.7/dop falhou")],
        indirect=True,
    )
    def test_detalhe_de_5xx_do_nucleo_nao_vaza(self, failing_client):
        """Mesmo redator do resto da borda (`_detail_for`), não um segundo."""
        r = failing_client.get("/api/v1/stream/events", headers=HEADERS)
        body = data([q for q in frames(r.text) if q.get("event") == "error"][0])
        assert body["detail"] == "internal error"
        assert body["retryable"] is False
        assert "senha" not in r.text and "10.0.0.7" not in r.text

    @pytest.mark.parametrize(
        "failing_client", [(grpc.StatusCode.NOT_FOUND, "demand não existe")], indirect=True
    )
    def test_o_que_ja_saiu_continua_valendo(self, failing_client):
        """O err não invalida os eventos entregues antes dele."""
        r = failing_client.get("/api/v1/stream/events", headers=HEADERS)
        names = [q.get("event") for q in frames(r.text) if q.get("event")]
        assert names == ["event", "error"]


class TestAutorizacao:
    """Stream não pode ser a porta que nasce aberta.

    Estas recusas acontecem ANTES do primeiro byte — o caso de uso é chamado
    dentro do handler, não dentro do gerador — e por isso são status HTTP de
    verdade, com body JSON, e não um 200 que morre no primeiro board.
    """

    def test_sem_token_e_401(self, client_sse):
        r = client_sse.get("/api/v1/stream/events")
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/json")

    def test_sem_conta_ativa_e_400(self, client_sse):
        r = client_sse.get(
            "/api/v1/stream/events", headers={"authorization": token_for()}
        )
        assert r.status_code == 400

    def test_recusa_nao_abre_assinatura_no_nucleo(self, client_sse, eventos):
        client_sse.get("/api/v1/stream/events", headers={"authorization": token_for()})
        assert eventos.calls == []


class TestDesconexao:
    """Cliente que some tem que matar a assinatura no núcleo.

    Stream que continua depois do client ir embora é vazamento de goroutine do
    outro lado da rede: o watcher fica no fan-out do núcleo para sempre.
    """

    async def test_abandonar_o_gerador_cancela_a_chamada(self, core, monkeypatch):
        fake = ServicoFalso("WatchEvents", [envelope("ev-1")], segura=True)
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)

        from app.platform.context import AuthContext, Principal, auth_ctx

        token = auth_ctx.set(
            AuthContext(principal=Principal(subject="s"), user_id="u-1", account_id="acct-1")
        )
        try:
            gerador = uc.watch_account_events()
            assert (await anext(gerador)).id == "ev-1"
            assert fake.last.cancelada is False
            # É isto que o EventSourceResponse faz quando vê `http.disconnect`.
            await gerador.aclose()
            assert fake.last.cancelada is True
        finally:
            auth_ctx.reset(token)

    async def test_cliente_grpc_que_cancela_encerra_a_assinatura(
        self, stub_stream, core, monkeypatch
    ):
        """O mesmo, ponta a ponta: o cancelamento vem do FIO, não do teste."""
        fake = ServicoFalso("WatchEvents", [envelope("ev-1")], segura=True)
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)

        call = stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=ACCOUNT)
        assert (await call.read()).id == "ev-1"
        call.cancel()

        for _ in range(100):
            if fake.calls and fake.last.cancelada:
                break
            await asyncio.sleep(0.01)
        assert fake.last.cancelada is True


# ── gRPC ────────────────────────────────────────────────────────────────────


class TestGRPC:
    async def test_watch_events(self, stub_stream, eventos):
        recebidos = [e async for e in stub_stream.WatchEvents(
            bff.WatchEventsRequest(), metadata=ACCOUNT
        )]
        assert [e.id for e in recebidos] == ["ev-1", "ev-2"]
        assert recebidos[0].payload["stage"] == "build"

    async def test_cursor_e_campo_porque_grpc_nao_tem_cabecalho(self, stub_stream, eventos):
        async for _ in stub_stream.WatchEvents(
            bff.WatchEventsRequest(since_event_id="ev-40"), metadata=ACCOUNT
        ):
            pass
        assert eventos.last.request.since_event_id == "ev-40"

    async def test_sem_token_e_unauthenticated(self, stub_stream, eventos):
        """O interceptor de auth passou a valer em streaming — antes não valia."""
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(
                bff.WatchEventsRequest(), metadata=metadata_for(None)
            ):
                pass
        assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert eventos.calls == []

    async def test_sem_conta_ativa_e_invalid_argument(self, stub_stream, eventos):
        """Mesma regra do REST (SP-0), traduzida para o status equivalente ao 400."""
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(
                bff.WatchEventsRequest(), metadata=metadata_for(token_for(), account_id="")
            ):
                pass
        assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    async def test_erro_depois_do_primeiro_item_vira_status(self, stub_stream, core, monkeypatch):
        """Aqui o status ainda cabe: em gRPC ele viaja nos trailers.

        É a diferença de transporte que justifica o SSE precisar de um evento
        `error` e o gRPC não.
        """
        fake = ServicoFalso(
            "WatchEvents",
            [envelope("ev-1")],
            err=grpc_error(grpc.StatusCode.UNAVAILABLE, "assinante lento demais"),
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)

        call = stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=ACCOUNT)
        assert (await call.read()).id == "ev-1"
        with pytest.raises(AioRpcError) as exc:
            await call.read()
        # O código é o sinal, e ele chega inteiro depois de um item já entregue.
        # O detalhe some pelo mesmo motivo do SSE: 503 do núcleo não se repete.
        assert exc.value.code() == grpc.StatusCode.UNAVAILABLE
        assert exc.value.details() == "internal error"

    async def test_detalhe_de_5xx_nao_vaza_nem_aqui(self, stub_stream, core, monkeypatch):
        fake = ServicoFalso(
            "WatchEvents", err=grpc_error(grpc.StatusCode.INTERNAL, "senha=hunter2")
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=ACCOUNT):
                pass
        assert exc.value.details() == "internal error"

    async def test_tail_logs(self, stub_stream, execucao):
        linhas = [
            ll
            async for ll in stub_stream.TailLogs(
                bff.TailLogsRequest(sandbox_id="sbx-1", source="app"), metadata=ACCOUNT
            )
        ]
        assert [ll.line for ll in linhas] == ["subiu"]
        assert execucao.last.request.sandbox_id == "sbx-1"
        assert execucao.last.request.source == "app"


class TestDemandaSemCursor:
    """`WatchDemand` não tem `since_event_id` no núcleo — e a borda não finge que tem."""

    def test_sse_da_demanda_nao_emite_id(self, client_demand):
        r = client_demand.get("/api/v1/stream/demands/dem-1", headers=HEADERS)
        eventos_sse = [q for q in frames(r.text) if q.get("event") == "event"]
        assert eventos_sse and all("id" not in q for q in eventos_sse)

    def test_a_demanda_vira_o_aggregate_id(self, client_demand):
        r = client_demand.get("/api/v1/stream/demands/dem-1", headers=HEADERS)
        body = data([q for q in frames(r.text) if q.get("event") == "event"][0])
        assert body["aggregate_id"] == "dem-1"
        assert body["id"] == ""

    def test_log_sai_com_nome_de_evento_proprio(self, cliente_log):
        """`log` e `event` são coisas diferentes na screen; separar é do protocolo."""
        r = cliente_log.get("/api/v1/stream/sandboxes/sbx-1/logs", headers=HEADERS)
        board = [q for q in frames(r.text) if q.get("event") == "log"][0]
        assert data(board)["line"] == "subiu"


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim.

    O alarme que dispara se alguém reimplementar a tradução no servicer ou no
    router em vez de no caso de uso.
    """

    async def test_o_mesmo_evento_sai_pelas_duas_portas(self, eventos, stub_stream):
        with TestClient(_app()) as client:
            rest = client.get("/api/v1/stream/events", headers=HEADERS)
        do_sse = [data(q) for q in frames(rest.text) if q.get("event") == "event"]
        do_grpc = [
            e
            async for e in stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=ACCOUNT)
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
