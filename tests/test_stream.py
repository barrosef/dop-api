"""Streaming on both ports: SSE for the browser, gRPC for the CLI and the agents.

The doubles and the fixtures live HERE, and not in `conftest.py`, on purpose:
conftest is shared territory and there is work happening in it in parallel. What
this file needs from conftest is only what already exists there (the identity
fake core, the token and the metadata) — nothing is added on that side.

The core NEVER comes up: the double is a fake server-streaming call that records
the request it received and whether it was cancelled. It is that second part
that makes it possible to prove the hardest thing to prove in a stream — that
the client going away kills the subscription over in the core, instead of
leaving it hanging forever.
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
from app.routers import stream as routes
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


class FakeStream:
    """ONE streaming subscription in the core.

    It imitates `grpc.aio`'s call object: it is iterable and has `cancel()`. What
    it keeps — the request it received and whether it was cancelled — is exactly
    what the tests need to look at.
    """

    def __init__(self, items, *, err=None, holds=False, pause=0.0, request=None, metadata=None):
        self.items = list(items)
        self.err = err
        # The interval between items — a real stream does not deliver everything
        # at once, and it is in the silence between events that the heartbeat has
        # to appear.
        self.pause = pause
        # `holds` keeps the subscription OPEN after the last item, like a real
        # stream in a period with nothing happening. Without it disconnection
        # cannot be tested: the stream would end on its own first.
        self.holds = asyncio.Event() if holds else None
        self.request = request
        self.metadata = dict(metadata or ())
        self.cancelled = False

    async def _iterate(self):
        for item in self.items:
            if self.pause:
                await asyncio.sleep(self.pause)
            yield item
        if self.err is not None:
            raise self.err
        if self.holds is not None:
            await self.holds.wait()

    def __aiter__(self):
        return self._iterate()

    def cancel(self):
        self.cancelled = True
        return True


class FakeService:
    """The core's streaming service: a NEW subscription per call.

    New on each call because the parity test subscribes twice (once per port)
    and a double that returned the same exhausted stream would make the second
    port look empty.
    """

    def __init__(self, rpc: str, items=(), *, err=None, holds=False, pause=0.0):
        self.items, self.err, self.holds, self.pause = list(items), err, holds, pause
        self.calls: list[FakeStream] = []
        setattr(self, rpc, self._abrir)

    def _abrir(self, request, *, metadata=None, **_):
        s = FakeStream(
            self.items,
            err=self.err,
            holds=self.holds,
            pause=self.pause,
            request=request,
            metadata=metadata,
        )
        self.calls.append(s)
        return s

    @property
    def last(self) -> FakeStream:
        assert self.calls, "the stream was not opened"
        return self.calls[-1]


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def events(core, monkeypatch):
    """EventService fake, com dois events e a assinatura encerrando sozinha."""
    fake = FakeService("WatchEvents", [envelope("ev-1"), envelope("ev-2")])
    monkeypatch.setattr(stubs, "event_stub", lambda: fake)
    return fake


@pytest.fixture
def demand(core, monkeypatch):
    fake = FakeService(
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
def execution(core, monkeypatch):
    fake = FakeService(
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
    """The application with the stream router mounted.

    The definitive registration is in `app/main.py`, which is not this agent's.
    Mounting it here proves the router down the real path (middlewares,
    decorators and all) without fighting over the file with whoever maintains
    it.
    """
    app = create_app()
    app.include_router(routes.router)
    return app


@pytest.fixture
def client_sse(events):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def client_demand(demand):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def client_log(execution):
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
async def stream_server(core):
    """A real gRPC server, with the StreamServicer and the SAME interceptors.

    It does not use `GrpcServer` because registering the StreamService there
    belongs to whoever maintains `app/grpcapi/server.py`. The interceptor stack
    is copied in the same order — it is what this file needs to exercise, since
    it is what gained the streaming support.
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
async def stub_stream(stream_server):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{stream_server}") as channel:
        yield bff_grpc.StreamServiceStub(channel)


# ── leitura do text SSE ────────────────────────────────────────────────────


def frames(text: str) -> list[dict]:
    """SSE text → a list of frames with the fields that matter.

    Written by hand on purpose: a third-party parser would hide precisely what
    we want to check (that the `id:` goes out, that the `event:` goes out with
    the right name).
    """
    out = []
    for raw in text.split("\r\n\r\n"):
        if not raw.strip():
            continue
        board: dict = {"comment": []}
        for linha in raw.split("\r\n"):
            if linha.startswith(": "):
                board["comment"].append(linha[2:])
            elif ": " in linha:
                key, value = linha.split(": ", 1)
                board[key] = value
        out.append(board)
    return out


def data(board: dict) -> dict:
    return json.loads(board["data"])


# ── SSE ─────────────────────────────────────────────────────────────────────


class TestSSE:
    def test_it_answers_text_event_stream(self, client_sse):
        r = client_sse.get("/api/v1/stream/events", headers=HEADERS)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        # No intermediate buffer: a proxy that accumulates turns real time into
        # a batch, and the cockpit sits still until the connection closes.
        assert r.headers["x-accel-buffering"] == "no"

    def test_it_opens_saying_how_often_to_reconnect(self, client_sse):
        r = client_sse.get("/api/v1/stream/events", headers=HEADERS)
        assert frames(r.text)[0]["retry"] == str(settings.sse_retry_ms)

    def test_the_sse_id_is_the_cores_event_id(self, client_sse):
        """The `id:` is what makes resuming work — and it is not ours.

        A local counter would be an id the core cannot translate into a position
        no log: na reconexão, `since_event_id` chegaria lá sem significado.
        """
        r = client_sse.get("/api/v1/stream/events", headers=HEADERS)
        eventos_sse = [q for q in frames(r.text) if q.get("event") == "event"]
        assert [q["id"] for q in eventos_sse] == ["ev-1", "ev-2"]
        assert data(eventos_sse[0])["type"] == "dop.demand.stage.advanced"
        assert data(eventos_sse[0])["payload"] == {"stage": "build"}

    def test_last_event_id_becomes_the_cores_cursor(self, client_sse, events):
        """The link the ADR asks for: the SSE header → the core's `since_event_id`."""
        client_sse.get(
            "/api/v1/stream/events", headers={**HEADERS, "Last-Event-ID": "ev-40"}
        )
        assert events.last.request.since_event_id == "ev-40"

    def test_the_query_serves_whoever_cannot_send_a_header(self, client_sse, events):
        """EventSource does not allow setting a header on the FIRST connection."""
        client_sse.get(
            "/api/v1/stream/events?since_event_id=ev-7", headers=HEADERS
        )
        assert events.last.request.since_event_id == "ev-7"

    def test_the_header_beats_the_query(self, client_sse, events):
        """The URL freezes at the moment the EventSource is created; the header
        does not.

        Preferir a query traria events já vistos de volta a cada reconexão.
        """
        client_sse.get(
            "/api/v1/stream/events?since_event_id=ev-7",
            headers={**HEADERS, "Last-Event-ID": "ev-40"},
        )
        assert events.last.request.since_event_id == "ev-40"

    def test_the_filters_cross_to_the_core(self, client_sse, events):
        client_sse.get(
            "/api/v1/stream/events?aggregate=demand&types=dop.demand.stage.advanced",
            headers=HEADERS,
        )
        request = events.last.request
        assert list(request.aggregate) == ["demand"]
        assert list(request.types) == ["dop.demand.stage.advanced"]

    def test_the_active_account_crosses_in_the_metadata(self, client_sse, events):
        client_sse.get("/api/v1/stream/events", headers=HEADERS)
        assert events.last.metadata is not None
        assert dict(events.last.metadata)["x-account-id"] == "acct-1"

    def test_the_heartbeat_goes_out_as_a_comment(self, core, monkeypatch):
        """`: ping` is an SSE comment: the browser ignores it, the proxy sees traffic.

        The double waits between one event and the next precisely to create the
        silence in which a proxy would drop the connection — that is where the
        ping has to appear.
        """
        monkeypatch.setattr(settings, "sse_ping_s", 0.01)
        monkeypatch.setattr(
            stubs,
            "event_stub",
            lambda: FakeService(
                "WatchEvents", [envelope("ev-1"), envelope("ev-2")], pause=0.05
            ),
        )
        with TestClient(_app()) as client:
            r = client.get("/api/v1/stream/events", headers=HEADERS)
        assert any(q["comment"] and q["comment"][0].startswith("ping") for q in frames(r.text))


class TestAnErrorAfterTheFirstByte:
    """A armadilha clássica: 200 já enviado, e aí o núcleo falha.

    There is no swapping the status after that. Either the error becomes an
    event the client knows how to read, or it becomes a stream that dies in
    silence — and a cockpit showing old data thinking it is live.
    """

    @pytest.fixture
    def failing_client(self, core, monkeypatch, request):
        code, detalhe = request.param
        fake = FakeService(
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
    def test_a_slow_consumer_becomes_a_retryable_error_event(self, failing_client):
        r = failing_client.get("/api/v1/stream/events", headers=HEADERS)
        # 200: the status was decided before the first event and does not change.
        assert r.status_code == 200
        err = [q for q in frames(r.text) if q.get("event") == "error"][0]
        body = data(err)
        assert body["code"] == "UNAVAILABLE"
        assert body["retryable"] is True
        # O cursor volta no body: quem reconecta por `fetch` (ou depois de um
        # an F5) has no automatic Last-Event-ID from the EventSource.
        assert body["since_event_id"] == "ev-1"
        # The text is OURS, not the core's: UNAVAILABLE is a 503, and
        # `_detail_for` does not let a 5xx detail from the core out. What carries
        # the instruction is the structured field, which is for machines.
        assert body["detail"] == routes._RECONNECT
        assert "assinante lento" not in r.text

    @pytest.mark.parametrize(
        "failing_client",
        [(grpc.StatusCode.INTERNAL, "pq://user:senha@10.0.0.7/dop falhou")],
        indirect=True,
    )
    def test_a_5xx_detail_from_the_core_does_not_leak(self, failing_client):
        """The same writer as the rest of the edge (`_detail_for`), not a second one."""
        r = failing_client.get("/api/v1/stream/events", headers=HEADERS)
        body = data([q for q in frames(r.text) if q.get("event") == "error"][0])
        assert body["detail"] == "internal error"
        assert body["retryable"] is False
        assert "senha" not in r.text and "10.0.0.7" not in r.text

    @pytest.mark.parametrize(
        "failing_client", [(grpc.StatusCode.NOT_FOUND, "the demand does not exist")], indirect=True
    )
    def test_what_has_already_gone_out_still_holds(self, failing_client):
        """The error does not invalidate the events delivered before it."""
        r = failing_client.get("/api/v1/stream/events", headers=HEADERS)
        names = [q.get("event") for q in frames(r.text) if q.get("event")]
        assert names == ["event", "error"]


class TestAuthorization:
    """A stream must not be the door that is born open.

    These refusals happen BEFORE the first byte — the use case is called inside
    the handler, not inside the generator — and so they are real HTTP statuses,
    with a JSON body, and not a 200 that dies at the first frame.
    """

    def test_with_no_token_it_is_a_401(self, client_sse):
        r = client_sse.get("/api/v1/stream/events")
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/json")

    def test_with_no_active_account_it_is_a_400(self, client_sse):
        r = client_sse.get(
            "/api/v1/stream/events", headers={"authorization": token_for()}
        )
        assert r.status_code == 400

    def test_a_refusal_opens_no_subscription_in_the_core(self, client_sse, events):
        client_sse.get("/api/v1/stream/events", headers={"authorization": token_for()})
        assert events.calls == []


class TestDisconnection:
    """A client that goes away has to kill the subscription in the core.

    A stream that carries on after the client has left is a goroutine leak on
    the other side of the network: the watcher stays in the core's fan-out
    forever.
    """

    async def test_abandoning_the_generator_cancels_the_call(self, core, monkeypatch):
        fake = FakeService("WatchEvents", [envelope("ev-1")], holds=True)
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)

        from app.platform.context import AuthContext, Principal, auth_ctx

        token = auth_ctx.set(
            AuthContext(principal=Principal(subject="s"), user_id="u-1", account_id="acct-1")
        )
        try:
            gerador = uc.watch_account_events()
            assert (await anext(gerador)).id == "ev-1"
            assert fake.last.cancelled is False
            # It is what the EventSourceResponse does when it sees
            # `http.disconnect`.
            await gerador.aclose()
            assert fake.last.cancelled is True
        finally:
            auth_ctx.reset(token)

    async def test_a_grpc_client_that_cancels_ends_the_subscription(
        self, stub_stream, core, monkeypatch
    ):
        """The same, end to end: the cancellation comes from the WIRE, not from the test."""
        fake = FakeService("WatchEvents", [envelope("ev-1")], holds=True)
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)

        call = stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=ACCOUNT)
        assert (await call.read()).id == "ev-1"
        call.cancel()

        for _ in range(100):
            if fake.calls and fake.last.cancelled:
                break
            await asyncio.sleep(0.01)
        assert fake.last.cancelled is True


# ── gRPC ────────────────────────────────────────────────────────────────────


class TestGRPC:
    async def test_watch_events(self, stub_stream, events):
        received = [e async for e in stub_stream.WatchEvents(
            bff.WatchEventsRequest(), metadata=ACCOUNT
        )]
        assert [e.id for e in received] == ["ev-1", "ev-2"]
        assert received[0].payload["stage"] == "build"

    async def test_the_cursor_is_a_field_because_grpc_has_no_header(self, stub_stream, events):
        async for _ in stub_stream.WatchEvents(
            bff.WatchEventsRequest(since_event_id="ev-40"), metadata=ACCOUNT
        ):
            pass
        assert events.last.request.since_event_id == "ev-40"

    async def test_with_no_token_it_is_unauthenticated(self, stub_stream, events):
        """The auth interceptor came to hold on streaming — it used not to."""
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(
                bff.WatchEventsRequest(), metadata=metadata_for(None)
            ):
                pass
        assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert events.calls == []

    async def test_with_no_active_account_it_is_invalid_argument(self, stub_stream, events):
        """The same rule as REST (SP-0), translated into the status equivalent to a 400."""
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(
                bff.WatchEventsRequest(), metadata=metadata_for(token_for(), account_id="")
            ):
                pass
        assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    async def test_an_error_after_the_first_item_becomes_a_status(self, stub_stream, core, monkeypatch):
        """Aqui o status ainda cabe: em gRPC ele viaja nos trailers.

        It is the transport difference that justifies SSE needing an `error`
        event and gRPC not.
        """
        fake = FakeService(
            "WatchEvents",
            [envelope("ev-1")],
            err=grpc_error(grpc.StatusCode.UNAVAILABLE, "assinante lento demais"),
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)

        call = stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=ACCOUNT)
        assert (await call.read()).id == "ev-1"
        with pytest.raises(AioRpcError) as exc:
            await call.read()
        # The code is the signal, and it arrives whole after an item has already
        # been delivered. The detail disappears for the same reason as in SSE: a
        # 503 from the core is not repeated.
        assert exc.value.code() == grpc.StatusCode.UNAVAILABLE
        assert exc.value.details() == "internal error"

    async def test_a_5xx_detail_does_not_leak_here_either(self, stub_stream, core, monkeypatch):
        fake = FakeService(
            "WatchEvents", err=grpc_error(grpc.StatusCode.INTERNAL, "senha=hunter2")
        )
        monkeypatch.setattr(stubs, "event_stub", lambda: fake)
        with pytest.raises(AioRpcError) as exc:
            async for _ in stub_stream.WatchEvents(bff.WatchEventsRequest(), metadata=ACCOUNT):
                pass
        assert exc.value.details() == "internal error"

    async def test_tail_logs(self, stub_stream, execution):
        linhas = [
            ll
            async for ll in stub_stream.TailLogs(
                bff.TailLogsRequest(sandbox_id="sbx-1", source="app"), metadata=ACCOUNT
            )
        ]
        assert [ll.line for ll in linhas] == ["subiu"]
        assert execution.last.request.sandbox_id == "sbx-1"
        assert execution.last.request.source == "app"


class TestADemandHasNoCursor:
    """`WatchDemand` has no `since_event_id` in the core — and the edge does not pretend it has."""

    def test_the_demands_sse_emits_no_id(self, client_demand):
        r = client_demand.get("/api/v1/stream/demands/dem-1", headers=HEADERS)
        eventos_sse = [q for q in frames(r.text) if q.get("event") == "event"]
        assert eventos_sse and all("id" not in q for q in eventos_sse)

    def test_the_demand_becomes_the_aggregate_id(self, client_demand):
        r = client_demand.get("/api/v1/stream/demands/dem-1", headers=HEADERS)
        body = data([q for q in frames(r.text) if q.get("event") == "event"][0])
        assert body["aggregate_id"] == "dem-1"
        assert body["id"] == ""

    def test_the_log_goes_out_with_its_own_event_name(self, client_log):
        """`log` and `event` are different things on the screen; separating them is the protocol's job."""
        r = client_log.get("/api/v1/stream/sandboxes/sbx-1/logs", headers=HEADERS)
        board = [q for q in frames(r.text) if q.get("event") == "log"][0]
        assert data(board)["line"] == "subiu"


class TestParityBetweenTransports:
    """One function, two adapters — and the proof that it stays that way.

    The alarm that fires if anybody reimplements the translation in the servicer
    or in the
    router em vez de no caso de uso.
    """

    async def test_the_same_event_goes_out_through_both_ports(self, events, stub_stream):
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
            # The time is the same instant in both clothes: ISO-8601 in the JSON,
            # Timestamp no protobuf.
            assert sse["occurred_at"].startswith("2023-11-14")
            assert rpc.occurred_at.seconds == 1_700_000_000
