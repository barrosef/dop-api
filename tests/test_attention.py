"""The attention box on both transports, against a fake core.

Four groups carry this file, and each protects a decision of the spec
(conversation-and-attention §3):

  - **the order is the core's.** The priority is DERIVED there (the kind's
    impact, then age) and is not recomputed here: a second ruler would make the
    queue stop having a single order. The test sends the queue shuffled on
    purpose — if the edge reordered, it would break;
  - **the badge is `open_total`.** Counting the page would give a number that
    changes when you paginate;
  - **what the contract does NOT offer.** There is no route and no RPC to "mark
    as read" or "dismiss": an item is born of and dies of an event, and an item
    that disappears with the problem unsolved is a comfortable lie (risk R-1).
    There is a test for the absence, because an absence nobody tests somebody
    "fixes";
  - **parity** REST × gRPC, the alarm that fires if the grouping or the names
    are reimplemented in an adapter.

The doubles and the fixtures live in THIS file, and not in `conftest.py`:
conftest is shared territory and there are other agents writing here right now.
What already exists there (the token, the metadata, FakeCall, `grpc_error`) is
imported.
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
from tests.conftest import PROJECT, FakeCall, grpc_error, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

Item = attention_pb2.AttentionItem


def _quando(segundos: int) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.FromSeconds(segundos)
    return ts


def item(
    id_: str,
    kind: int,
    *,
    priority: int,
    demand: str = "",
    target: tuple[str, str] = ("thread", "th-1"),
    resolved_update: bool = False,
) -> Item:
    """Um item como o núcleo o returns — com a priority JÁ derivada por ele."""
    msg = Item(
        id=id_,
        account=common_pb2.AccountRef(id="acct-1"),
        kind=kind,
        target_kind=target[0],
        target_id=target[1],
        title=f"título de {id_}",
        summary=f"resumo de {id_}",
        priority=priority,
        opened_at=_quando(1_700_000_000),
    )
    # Absent ≠ zeroed: an account item belongs to no demand, and an empty
    # DemandRef would make the edge group under a demand with id "".
    if demand:
        msg.demand.CopyFrom(common_pb2.DemandRef(id=demand))
    if resolved_update:
        msg.resolved_at.CopyFrom(_quando(1_700_003_600))
    return msg


# The queue as the core delivers it: ALREADY ORDERED by it, and deliberately in
# an order no obvious rule of the edge would reproduce by accident — demand dem-2
# appears before dem-1, and the account item (with no demand) comes in the
# middle. If the edge reordered by priority, by demand or by id, it would break.
QUEUE = [
    item("at-1", Item.KIND_MERGE_CONFLICT, priority=10_100, demand="dem-2"),
    item("at-2", Item.KIND_INTEGRATION_BROKEN, priority=20_100,
         target=("resource", "res-1")),
    item("at-3", Item.KIND_GATE_PENDING, priority=40_050, demand="dem-1",
         target=("stage", "spec")),
    item("at-4", Item.KIND_THREAD_BLOCKED, priority=70_010, demand="dem-2"),
    item("at-5", Item.KIND_THREAD_BLOCKED, priority=70_120, demand="dem-1"),
]


# ── doubles do núcleo ────────────────────────────────────────────────────────


class StreamFalso:
    """UMA assinatura de streaming no núcleo.

    Imita o objeto de call do `grpc.aio`: iterável e com `cancel()`. Guarda o
    request it received and whether it was cancelled — it is the second part that
    makes it possible to prove the hardest thing to prove in a stream: that the
    client going away kills the subscription over there, instead of leaving it
    hanging forever.
    """

    def __init__(self, items, *, err=None, segura=False, request=None, metadata=None):
        self.items = list(items)
        self.err = err
        # `segura` mantém a assinatura ABERTA depois do último item, como um
        # real stream in a period with nothing happening. Without it there is no
        # way to
        # testar desconexão: o stream acabaria sozinho antes.
        self.segura = asyncio.Event() if segura else None
        self.request = request
        self.metadata = dict(metadata or ())
        self.cancelada = False

    async def _iterate(self):
        for i in self.items:
            yield i
        if self.err is not None:
            raise self.err
        if self.segura is not None:
            await self.segura.wait()

    def __aiter__(self):
        return self._iterate()

    def cancel(self):
        self.cancelada = True
        return True


class AtencaoFalsa:
    """The attention box's fake core.

    `ListAttention` returns a queue ALREADY ordered and an `open_total` LARGER
    than the number of items on the page — that is how it is proven the badge is
    not a
    contagem da página disfarçada.

    `WatchAttention` opens a NEW subscription per call, because the parity test
    subscribes twice (once per port) and a double that returned the same
    stream esgotado faria a segunda porta parecer vazia.
    """

    # Aberto na account inteira: sete, contra os cinco desta página.
    OPEN_TOTAL = 7

    def __init__(self, items=QUEUE, atualizacoes=(), *, err=None, segura=False):
        self.ListAttention = FakeCall(
            attention_pb2.ListAttentionResponse(
                items=items, open_total=self.OPEN_TOTAL
            )
        )
        self.atualizacoes = list(atualizacoes)
        self.err, self.segura = err, segura
        self.streams: list[StreamFalso] = []

    def WatchAttention(self, request, *, metadata=None, **_):
        s = StreamFalso(
            self.atualizacoes,
            err=self.err,
            segura=self.segura,
            request=request,
            metadata=metadata,
        )
        self.streams.append(s)
        return s

    @property
    def ultimo_stream(self) -> StreamFalso:
        assert self.streams, "the stream was not opened"
        return self.streams[-1]


def aberto(it: Item) -> attention_pb2.AttentionUpdate:
    return attention_pb2.AttentionUpdate(
        change=attention_pb2.AttentionUpdate.CHANGE_OPENED, item=it
    )


def resolved_update(kind: int, target: tuple[str, str]) -> attention_pb2.AttentionUpdate:
    """The close notice as the core sends it: PARTIAL, by the target.

    Whoever closes it knows the target, not the projection's id — so no id
    comes, no title, no `opened_at` and no `resolved_at`. It is the real shape
    (internal/domain/attention/service.go), and imitating a whole item here would
    hide that the client has to match by the target.
    """
    return attention_pb2.AttentionUpdate(
        change=attention_pb2.AttentionUpdate.CHANGE_RESOLVED,
        item=Item(
            account=common_pb2.AccountRef(id="acct-1"),
            kind=kind,
            target_kind=target[0],
            target_id=target[1],
        ),
    )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def attention(core, monkeypatch):
    fake = AtencaoFalsa()
    monkeypatch.setattr(stubs, "attention_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app real do BFF, com as rotas deste domínio registradas.

    `app/main.py` belongs to the repository's owner and does not include this
    router yet (see the
    relatório). Montar aqui exercita o app de verdade — middlewares, decorators
    e tudo — sem disputar o arquivo com quem o mantém. O `if` deixa o teste
    stay correct once the registration lands in `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/attention" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client(attention):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub(attention):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    It does not reuse conftest's `grpc_server` because `GrpcServer` does not
    register this servicer yet (the registration is the repository owner's). The
    interceptor stack is the SAME, in the same order — it is what makes the
    token, the context and
    decorators valerem dentro do servicer, inclusive em streaming.
    """
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_AttentionServiceServicer_to_server(AttentionServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.AttentionServiceStub(channel)
    finally:
        await server.stop(0)


def com_stream(monkeypatch, atualizacoes=(), **kwargs) -> AtencaoFalsa:
    fake = AtencaoFalsa(atualizacoes=atualizacoes, **kwargs)
    monkeypatch.setattr(stubs, "attention_stub", lambda: fake)
    return fake


# ── leitura do text SSE ────────────────────────────────────────────────────


def frames(text: str) -> list[dict]:
    """SSE text → frames. By hand on purpose: a third-party parser would hide
    precisely what we want to check (that the `id:` does NOT go out, that the
    `event:` goes out with the right name)."""
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


def atencoes(text: str) -> list[dict]:
    return [data(q) for q in frames(text) if q.get("event") == rotas.ATTENTION]


# ── a queue ──────────────────────────────────────────────────────────────────


class TestOrderAndBadge:
    """The two things the edge does NOT decide: the order and the badge."""

    def test_the_order_is_the_cores(self, client):
        """The priority is derived there; a second ruler here would puncture the queue.

        The double's queue is deliberately shuffled with respect to demand and
        id: if
        a borda ordenasse por qualquer critério próprio, a lista sairia diferente
        desta.
        """
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        assert [i["id"] for i in box["items"]] == [
            "at-1", "at-2", "at-3", "at-4", "at-5"
        ]
        assert [i["priority"] for i in box["items"]] == [
            10_100, 20_100, 40_050, 70_010, 70_120
        ]

    def test_the_priority_crosses_without_being_recomputed(self, client, attention):
        """An absurd priority stays absurd: the edge does not fix the ruler.

        Se ela recalculasse (por idade, por tipo, por qualquer coisa), este
        number would change — and there would come to be two rulers for the same
        queue.
        """
        attention.ListAttention.returns(
            attention_pb2.ListAttentionResponse(
                items=[item("at-9", Item.KIND_PR_REVIEW, priority=999_999)],
                open_total=1,
            )
        )
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        assert box["items"][0]["priority"] == 999_999

    def test_the_badge_is_the_cores_and_not_the_pages_count(self, client):
        """Counting the page would give a badge that changes when you paginate."""
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        assert box["open_total"] == AtencaoFalsa.OPEN_TOTAL
        assert len(box["items"]) == 5
        assert box["open_total"] != len(box["items"])

    def test_filtering_by_demand_does_not_touch_the_badge(self, client, attention):
        """`open_total` is the ACCOUNT's — it is the bell's number, not the open screen's."""
        box = client.get(
            "/api/v1/attention?demand_id=dem-1", headers=HEADERS
        ).json()
        assert box["open_total"] == AtencaoFalsa.OPEN_TOTAL
        assert attention.ListAttention.requests[0].demand.id == "dem-1"

    def test_with_no_filter_the_demand_is_not_filled_in(self, client, attention):
        """An empty DemandRef would be a demand with id "" — a filter, not the
        absence
        de filtro."""
        client.get("/api/v1/attention", headers=HEADERS)
        assert not attention.ListAttention.requests[0].HasField("demand")

    def test_resolved_items_only_on_an_explicit_request(self, client, attention):
        client.get("/api/v1/attention", headers=HEADERS)
        assert attention.ListAttention.requests[0].include_resolved is False
        client.get("/api/v1/attention?include_resolved=true", headers=HEADERS)
        assert attention.ListAttention.requests[1].include_resolved is True

    def test_the_page_size_crosses(self, client, attention):
        client.get("/api/v1/attention?page_size=20", headers=HEADERS)
        assert attention.ListAttention.requests[0].page.size == 20


class TestGroupingByDemand:
    """What the edge ADDS — and what it never stops preserving."""

    def test_it_groups_by_demand_preserving_the_order(self, client):
        """Os grupos saem na ordem do item mais urgente de cada um.

        Which is the order of the first appearance in the queue — no new
        comparison. A
        `sorted` daria o mesmo result hoje e passaria a divergir no dia em que
        a régua do núcleo mudasse.
        """
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        assert [g["demand_id"] for g in box["groups"]] == ["dem-2", "", "dem-1"]
        por_demanda = {g["demand_id"]: [i["id"] for i in g["items"]] for g in box["groups"]}
        assert por_demanda["dem-2"] == ["at-1", "at-4"]
        assert por_demanda["dem-1"] == ["at-3", "at-5"]

    def test_an_account_item_is_a_group_and_not_a_leftover(self, client):
        """A broken integration affects the whole account: it is among the most
        urgent items there are, and throwing it into an "others" footer would hide
        it precisely when
        nada mais anda."""
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        account = [g for g in box["groups"] if g["demand_id"] == ""][0]
        assert [i["id"] for i in account["items"]] == ["at-2"]
        assert account["items"][0]["target_kind"] == "resource"
        # And the item stays in the flat queue too: grouping is not moving.
        assert "at-2" in [i["id"] for i in box["items"]]

    def test_the_groups_contain_the_same_items_as_the_queue(self, client):
        """Grouping must neither lose nor duplicate an item — it would be a queue
        lying
        sobre si mesma em dois lugares da mesma response."""
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        grouped = [i["id"] for g in box["groups"] for i in g["items"]]
        assert sorted(grouped) == sorted(i["id"] for i in box["items"])


class TestWhatTheContractDoesNotOffer:
    """The absence is the decision, and that is why it is tested.

    The box is a PROJECTION: an item is born of an event and dies of an event,
    and what closes it is the fact — the gate decided, the thread unblocked. A
    "dismiss" at the edge would take the item off the screen without taking the
    problem out of the world, and a box that lies becomes
    box ignorada (risco R-1 da spec).
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
    def test_there_is_no_mark_as_read_and_no_dismiss(self, client, metodo, caminho):
        r = getattr(client, metodo)(caminho, headers=HEADERS)
        assert r.status_code in (404, 405)

    def test_the_grpc_service_is_read_only(self):
        """O mesmo, no contrato: dois RPCs, os dois de leitura."""
        servico = bff.DESCRIPTOR.services_by_name["AttentionService"]
        assert {m.name for m in servico.methods} == {"ListAttention", "WatchAttention"}


class TestTheItemAtTheEdge:
    def test_the_target_is_a_pair_and_not_a_finished_route(self, client):
        """Guardar a rota pronta amarraria o backend ao desenho da screen."""
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        portao = [i for i in box["items"] if i["id"] == "at-3"][0]
        assert (portao["target_kind"], portao["target_id"]) == ("stage", "spec")
        assert portao["kind"] == "gate_pending"

    def test_an_open_item_has_no_resolution_date(self, client):
        """None, not epoch zero: 1970 in a queue ordered by age would show up
        como o item mais antigo do mundo."""
        box = client.get("/api/v1/attention", headers=HEADERS).json()
        assert all(i["resolved_at"] is None for i in box["items"])
        assert all(i["opened_at"] is not None for i in box["items"])

    def test_a_resolved_item_brings_the_date(self, client, attention):
        """A resolved item leaves the box but stays in the projection: it is what
        tells how long the dev took to answer."""
        attention.ListAttention.returns(
            attention_pb2.ListAttentionResponse(
                items=[
                    item("at-8", Item.KIND_PR_REVIEW, priority=50_000, resolved_update=True)
                ],
                open_total=0,
            )
        )
        box = client.get(
            "/api/v1/attention?include_resolved=true", headers=HEADERS
        ).json()
        assert box["items"][0]["resolved_at"] is not None


# ── o stream ────────────────────────────────────────────────────────────────


class TestSSE:
    def test_it_answers_text_event_stream(self, core, monkeypatch):
        com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        # No intermediate buffer: a proxy that accumulates turns real time into
        # a batch, and the box sits still until the connection closes.
        assert r.headers["x-accel-buffering"] == "no"

    def test_it_opens_saying_how_often_to_reconnect(self, core, monkeypatch):
        com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        assert frames(r.text)[0]["retry"] == str(settings.sse_retry_ms)

    def test_its_own_event_name(self, core, monkeypatch):
        """`attention` and not `event`: the body is a CHANGE in the queue, not an
        event from the log. They are different renderers on the screen — the same
        reason `log` is already separate from `event`."""
        com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        assert [q.get("event") for q in frames(r.text) if q.get("event")] == ["attention"]

    def test_the_opening_brings_the_whole_item(self, core, monkeypatch):
        com_stream(monkeypatch, [aberto(QUEUE[2])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        (upd,) = atencoes(r.text)
        assert upd["change"] == "opened"
        assert upd["item"]["id"] == "at-3"
        assert upd["item"]["kind"] == "gate_pending"
        assert upd["item"]["demand_id"] == "dem-1"

    def test_the_close_identifies_by_target_and_the_edge_invents_no_id(
        self, core, monkeypatch
    ):
        """The core closes by the TARGET — whoever closes it knows the target, not the id.

        A borda repassa como está. Fabricar um id aqui, ou carimbar
        `resolved_at` with the BFF's clock, would give the cockpit a datum nobody
        measured: what matches the notice with the item on the screen is the
        (kind, target) pair, and
        what says it closed is the `change`.
        """
        com_stream(monkeypatch, [resolved_update(Item.KIND_THREAD_BLOCKED, ("thread", "th-1"))])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        (upd,) = atencoes(r.text)
        assert upd["change"] == "resolved"
        assert upd["item"]["id"] == ""
        assert upd["item"]["resolved_at"] is None
        assert (upd["item"]["kind"], upd["item"]["target_id"]) == (
            "thread_blocked",
            "th-1",
        )

    def test_last_event_id_becomes_the_cores_cursor(self, core, monkeypatch):
        """The link ADR-0017 asks for: the SSE header → `since_event_id`."""
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get(
                "/api/v1/stream/attention",
                headers={**HEADERS, "Last-Event-ID": "ev-40"},
            )
        assert fake.ultimo_stream.request.since_event_id == "ev-40"

    def test_the_header_beats_the_query(self, core, monkeypatch):
        """The URL freezes when the EventSource is created; the header does not.

        Preferir a query traria mudanças já vistas de volta a cada reconexão.
        """
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get(
                "/api/v1/stream/attention?since_event_id=ev-7",
                headers={**HEADERS, "Last-Event-ID": "ev-40"},
            )
        assert fake.ultimo_stream.request.since_event_id == "ev-40"

    def test_the_query_serves_whoever_cannot_send_a_header(self, core, monkeypatch):
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get("/api/v1/stream/attention?since_event_id=ev-7", headers=HEADERS)
        assert fake.ultimo_stream.request.since_event_id == "ev-7"

    def test_it_emits_no_id_because_the_core_does_not_send_the_event_id(
        self, core, monkeypatch
    ):
        """`dop.v1.AttentionUpdate` does not carry the id of the event that produced it.

        Without it there is no HONEST `id:` to emit. Using the ITEM's id would be
        worse than emitting none: it is not a position in the log, and it would
        go back to the core as a meaningless cursor on the first reconnection.
        This test is what stops
        alguém de "consertar" a retomada assim.
        """
        com_stream(monkeypatch, [aberto(QUEUE[0]), aberto(QUEUE[2])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        eventos = [q for q in frames(r.text) if q.get("event") == rotas.ATTENTION]
        assert eventos and all("id" not in q for q in eventos)

    def test_the_active_account_crosses_in_the_metadata(self, core, monkeypatch):
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            c.get("/api/v1/stream/attention", headers=HEADERS)
        assert fake.ultimo_stream.metadata["x-account-id"] == "acct-1"


class TestAnErrorAfterTheFirstByte:
    """The 200 has already been sent, and then the core fails. There is no swapping the status.

    The same mechanics as the event stream hold, because it is literally the
    same
    código (`routers/stream._eventos_sse`): o err vira `event: error` em vez de
    a stream that dies in silence.
    """

    def test_a_slow_consumer_becomes_a_retryable_error_event(self, core, monkeypatch):
        com_stream(
            monkeypatch,
            [aberto(QUEUE[0])],
            err=grpc_error(grpc.StatusCode.UNAVAILABLE, "assinante lento demais"),
        )
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        assert r.status_code == 200
        body = data([q for q in frames(r.text) if q.get("event") == "error"][0])
        assert body["code"] == "UNAVAILABLE"
        assert body["retryable"] is True

    def test_a_5xx_detail_from_the_core_does_not_leak(self, core, monkeypatch):
        """The same writer as the rest of the edge (`_detail_for`), not a second one."""
        com_stream(
            monkeypatch,
            [aberto(QUEUE[0])],
            err=grpc_error(grpc.StatusCode.INTERNAL, "pq://user:senha@10.0.0.7 caiu"),
        )
        with TestClient(_app_com_rotas()) as c:
            r = c.get("/api/v1/stream/attention", headers=HEADERS)
        body = data([q for q in frames(r.text) if q.get("event") == "error"][0])
        assert body["detail"] == "internal error"
        assert "senha" not in r.text and "10.0.0.7" not in r.text


class TestAuthorization:
    """Neither the box nor the stream may be the door that is born open."""

    def test_listing_with_no_token_is_a_401(self, client):
        assert client.get("/api/v1/attention").status_code == 401

    def test_listing_with_no_active_account_is_a_400(self, client):
        r = client.get("/api/v1/attention", headers={"authorization": token_for()})
        assert r.status_code == 400

    def test_a_stream_with_no_active_account_is_a_400_and_not_an_empty_200(self, core, monkeypatch):
        """The refusal happens BEFORE the first byte: the use case is called
        inside the handler, not inside the generator."""
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])])
        with TestClient(_app_com_rotas()) as c:
            r = c.get(
                "/api/v1/stream/attention", headers={"authorization": token_for()}
            )
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/json")
        # And no subscription was opened in the core.
        assert fake.streams == []


class TestDisconnection:
    """A client that goes away has to kill the subscription in the core.

    A stream that carries on after the client has left is a goroutine leak on
    the other side of the network: the watcher stays in the core's fan-out
    forever.
    """

    async def test_abandoning_the_generator_cancels_the_call(self, core, monkeypatch):
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])], segura=True)

        from app.platform.context import AuthContext, Principal, auth_ctx

        token = auth_ctx.set(
            AuthContext(principal=Principal(subject="s"), user_id="u-1", account_id="acct-1")
        )
        try:
            gerador = uc.watch_attention()
            assert (await anext(gerador)).item.id == "at-1"
            assert fake.ultimo_stream.cancelada is False
            # It is what the EventSourceResponse does when it sees
            # `http.disconnect`.
            await gerador.aclose()
            assert fake.ultimo_stream.cancelada is True
        finally:
            auth_ctx.reset(token)


# ── gRPC ────────────────────────────────────────────────────────────────────


class TestGRPC:
    async def test_the_list_comes_with_groups_and_a_badge(self, stub):
        resp = await stub.ListAttention(bff.ListAttentionRequest(), metadata=ACCOUNT)
        assert [i.id for i in resp.items] == ["at-1", "at-2", "at-3", "at-4", "at-5"]
        assert [g.demand_id for g in resp.groups] == ["dem-2", "", "dem-1"]
        assert resp.open_total == AtencaoFalsa.OPEN_TOTAL

    async def test_an_open_item_has_no_resolution_field(self, stub):
        """Ausente ≠ zerado também na volta: um Timestamp zerado diria
        "resolved_update em 1970", e o HasField do outro lado confirmaria."""
        resp = await stub.ListAttention(bff.ListAttentionRequest(), metadata=ACCOUNT)
        assert resp.items[0].HasField("opened_at")
        assert not resp.items[0].HasField("resolved_at")

    async def test_watch_brings_the_opening_and_the_close(self, core, monkeypatch, stub):
        com_stream(
            monkeypatch,
            [aberto(QUEUE[0]), resolved_update(Item.KIND_MERGE_CONFLICT, ("pull_request", "pr-9"))],
        )
        received = [
            u
            async for u in stub.WatchAttention(
                bff.WatchAttentionRequest(), metadata=ACCOUNT
            )
        ]
        assert [u.change for u in received] == [
            bff.ATTENTION_CHANGE_OPENED,
            bff.ATTENTION_CHANGE_RESOLVED,
        ]
        assert received[0].item.kind == bff.ATTENTION_KIND_MERGE_CONFLICT
        # The close identifies by the target, and the edge invents no id.
        assert received[1].item.id == ""
        assert received[1].item.target_id == "pr-9"

    async def test_the_cursor_is_a_field_because_grpc_has_no_header(
        self, core, monkeypatch, stub
    ):
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])])
        async for _ in stub.WatchAttention(
            bff.WatchAttentionRequest(since_event_id="ev-40"), metadata=ACCOUNT
        ):
            pass
        assert fake.ultimo_stream.request.since_event_id == "ev-40"

    async def test_with_no_token_it_is_unauthenticated(self, stub, attention):
        with pytest.raises(AioRpcError) as e:
            await stub.ListAttention(
                bff.ListAttentionRequest(), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert attention.ListAttention.calls == []

    async def test_a_stream_with_no_active_account_is_invalid_argument(
        self, core, monkeypatch, stub
    ):
        """Mesma regra do REST (SP-0), no status equivalente ao 400."""
        fake = com_stream(monkeypatch, [aberto(QUEUE[0])])
        with pytest.raises(AioRpcError) as e:
            async for _ in stub.WatchAttention(
                bff.WatchAttentionRequest(),
                metadata=metadata_for(token_for(), account_id=""),
            ):
                pass
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert fake.streams == []

    async def test_a_core_error_crosses_with_its_own_status(self, stub, attention):
        attention.ListAttention.fails_with(grpc.StatusCode.NOT_FOUND, "account sumiu")
        with pytest.raises(AioRpcError) as e:
            await stub.ListAttention(bff.ListAttentionRequest(), metadata=ACCOUNT)
        assert e.value.code() == grpc.StatusCode.NOT_FOUND

    async def test_a_5xx_detail_does_not_leak_here_either(self, stub, attention):
        attention.ListAttention.fails_with(grpc.StatusCode.INTERNAL, "senha=hunter2")
        with pytest.raises(AioRpcError) as e:
            await stub.ListAttention(bff.ListAttentionRequest(), metadata=ACCOUNT)
        assert e.value.details() == "internal error"


class TestParityBetweenTransports:
    """One function, two adapters — and the proof that it stays that way.

    The alarm that fires if anybody reimplements the grouping (or the kind
    names
    tipo) num adaptador em vez de no caso de uso.
    """

    async def test_the_box_is_the_same_on_both_ports(self, client, stub):
        rest = client.get("/api/v1/attention", headers=HEADERS).json()
        grpc_resp = await stub.ListAttention(bff.ListAttentionRequest(), metadata=ACCOUNT)

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
        # And the optional part, which is where absent ≠ zeroed may diverge
        # between the ends.
        for i_grpc, i_rest in zip(grpc_resp.items, rest["items"], strict=True):
            assert i_grpc.HasField("resolved_at") == (i_rest["resolved_at"] is not None)
            assert i_grpc.HasField("opened_at") == (i_rest["opened_at"] is not None)

    async def test_the_same_change_goes_out_through_both_ports(self, core, monkeypatch, stub):
        atualizacoes = [
            aberto(QUEUE[0]),
            resolved_update(Item.KIND_THREAD_BLOCKED, ("thread", "th-1")),
        ]
        com_stream(monkeypatch, atualizacoes)
        with TestClient(_app_com_rotas()) as c:
            do_sse = atencoes(
                c.get("/api/v1/stream/attention", headers=HEADERS).text
            )
        do_grpc = [
            u
            async for u in stub.WatchAttention(
                bff.WatchAttentionRequest(), metadata=ACCOUNT
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
            # The kind's name at the REST edge and the enum over gRPC are the
            # same fact.
            assert rpc.item.kind == Item.Kind.Value(
                "KIND_" + sse["item"]["kind"].upper()
            )
