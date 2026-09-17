"""Delivery on both transports, against a fake core.

This file's centre is the **refusal for want of green** (ADR-0005): the core
refuses the entry into the queue saying, item by item, what is missing, and that
list is the useful part of the response. The tests cover the three ways of
ruining it — losing it (turning it into a "no"), flattening it (turning it into a
single sentence) and swallowing TOO MUCH (treating any core error as a refusal).

The doubles and the fixtures live in THIS file (conftest is shared territory);
what already exists there is imported.
"""

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, delivery_pb2, identity_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.delivery import DeliveryServicer
from app.grpcapi.gen.dop.bff.v1 import delivery_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import delivery_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import delivery as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# The exact sentence the core writes when refusing an entry into the queue
# (internal/domain/delivery/service.go + Evidence.Missing).
REFUSAL = (
    "the merge queue refuses an entry with no evidence of green for commit abc1234 (ADR-0005): "
    "no passed acceptance run for commit abc1234 (ADR-0005 §1); "
    "the critic's opinion for commit abc1234 (ADR-0005 §3) is missing"
)


def viewer_role(core) -> None:
    """Demotes the actor to viewer — whoever reads the delivery but does not push it.

    By changing what ListMemberships answers, which is where the role comes from
    at login; a shortcut in the context would prove less than it seems.
    """
    core.ListMemberships = FakeCall(
        identity_pb2.ListMembershipsResponse(
            memberships=[
                identity_pb2.Membership(
                    id="m-1",
                    user=common_pb2.UserRef(id=core.user_id),
                    account=common_pb2.AccountRef(id=core.account.id),
                    role=identity_pb2.ROLE_VIEWER,
                )
            ]
        )
    )


class EntregasFalsas:
    """Núcleo fake de entrega."""

    def __init__(self):
        pr = delivery_pb2.PullRequest(
            id="pr-1",
            demand=common_pb2.DemandRef(id="dem-1"),
            repo="acme/portal",
            source_branch="dop/dem-1",
            target_branch="main",
            url="https://git/acme/portal/pull/7",
            reviewers=[
                delivery_pb2.Reviewer(name="Ana", initials="AS", status="approved"),
                delivery_pb2.Reviewer(name="Bruno", initials="BC", status="pending"),
                delivery_pb2.Reviewer(name="Caio", initials="CL", status="pending"),
            ],
        )
        entry = delivery_pb2.MergeQueueEntry(
            id="mq-1",
            repo_id="repo-1",
            demand=common_pb2.DemandRef(id="dem-1"),
            position=1,
            state=delivery_pb2.MergeQueueEntry.STATE_QUEUED,
            overlapping_files=["app/main.py"],
        )
        directive = delivery_pb2.Directive(
            id="dir-1",
            project=common_pb2.ProjectRef(id="prj-1"),
            kind=delivery_pb2.Directive.KIND_CHERRY_PICK,
            decided_by=common_pb2.ActorRef(
                kind=common_pb2.ActorRef.KIND_USER, id="u-1", name="Dev"
            ),
        )
        directive.payload.update({"from": "dem-0", "to": "dem-1"})

        self.pr, self.entry, self.directive = pr, entry, directive
        self.ListPullRequests = FakeCall(
            delivery_pb2.ListPullRequestsResponse(pull_requests=[pr])
        )
        self.GetMergeQueue = FakeCall(
            delivery_pb2.GetMergeQueueResponse(entries=[entry])
        )
        self.EnqueueMerge = FakeCall(entry)
        self.ListDirectives = FakeCall(
            delivery_pb2.ListDirectivesResponse(directives=[directive])
        )
        self.DecideDirective = FakeCall(directive)

    def sem_verde(self) -> None:
        """The core refuses the entry into the queue, listing what is missing."""
        self.EnqueueMerge.fails_with(grpc.StatusCode.FAILED_PRECONDITION, REFUSAL)


@pytest.fixture
def deliveries(core, monkeypatch):
    fake = EntregasFalsas()
    monkeypatch.setattr(stubs, "delivery_stub", lambda: fake)
    return fake


def _app_with_routes():
    """The app with the delivery routes.

    `app/main.py` is not this agent's: until the registration lands there, the
    test assembles the app and adds the router. The `if` keeps the test valid
    after the registration, with no duplicated route.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/delivery/board" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_del(deliveries):
    with TestClient(_app_with_routes()) as c:
        yield c


@pytest.fixture
async def stub_ent(deliveries):
    """A real gRPC server, on an ephemeral port, with the SAME interceptors as
    the production port. Its own because `app/grpcapi/server.py` does not register
    this servicer yet, and that file is not this agent's."""
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_DeliveryServiceServicer_to_server(DeliveryServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.DeliveryServiceStub(channel)
    finally:
        await server.stop(0)


class TestWithoutGreen:
    """The queue's refusal — what the dev needs to read, whole."""

    def test_rest_returns_a_412_with_the_list_of_what_is_missing(self, client_del, deliveries):
        deliveries.sem_verde()
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 412
        detalhe = r.json()["detail"]
        assert detalhe["missing"] == [
            "no passed acceptance run for commit abc1234 (ADR-0005 §1)",
            "the critic's opinion for commit abc1234 (ADR-0005 §3) is missing",
        ]
        # The reason keeps the commit: without it, "green is missing" does not
        # say WHICH code is being talked about — and green is always about a
        # commit.
        assert "abc1234" in detalhe["reason"]

    async def test_grpc_returns_the_refusal_as_a_response(self, stub_ent, deliveries):
        """For the agent, 'not yet, this is missing' is work to do, not a failure
        (ADR-0005 §2) — that is why the refusal is a field, and not an error
        status."""
        deliveries.sem_verde()
        resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.HasField("refusal")
        assert not resp.HasField("entry")
        assert len(resp.refusal.missing) == 2

    def test_a_refusal_with_no_list_invents_no_items(self, client_del, deliveries):
        """Not every precondition is about green: an already merged PR has a
        reason and nothing else. A one-item list repeating the reason would be
        noise."""
        deliveries.EnqueueMerge.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION, "the PR of demand dem-1 has already been merged"
        )
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 412
        assert r.json()["detail"] == {
            "reason": "the PR of demand dem-1 has already been merged",
            "missing": [],
        }

    def test_another_core_error_does_not_become_a_refusal(self, client_del, deliveries):
        """A broad `except` would turn NOT_FOUND into 'green is missing' — and the
        dev would look for a test run for a repository that does not exist."""
        deliveries.EnqueueMerge.fails_with(grpc.StatusCode.NOT_FOUND, "repository not found")
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 404

    def test_a_5xx_detail_from_the_core_does_not_leak(self, client_del, deliveries):
        """Diverting the error from the normal path must not divert the writer: a
        internal failure of the core may carry a host, a query or a credential."""
        deliveries.EnqueueMerge.fails_with(grpc.StatusCode.INTERNAL, "dsn=postgres://user:password@db")
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 500
        assert r.json()["detail"] == "internal error"

    async def test_the_refusals_parity_between_the_ports(self, client_del, stub_ent, deliveries):
        """The list is the SAME on both transports; only the wrapper changes."""
        deliveries.sem_verde()
        rest = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        ).json()["detail"]
        grpc_resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=ACCOUNT
        )
        assert list(grpc_resp.refusal.missing) == rest["missing"]
        assert grpc_resp.refusal.reason == rest["reason"]


class TestREST:
    def test_the_board_joins_prs_and_directives(self, client_del, deliveries):
        r = client_del.get(
            "/api/v1/delivery/board?project_id=prj-1", headers=REST_HEADERS
        )
        assert r.status_code == 200
        body = r.json()
        assert [p["id"] for p in body["pull_requests"]] == ["pr-1"]
        assert [d["kind"] for d in body["directives"]] == ["cherry_pick"]
        # Two calls to the core, one response to the client.
        assert len(deliveries.ListPullRequests.calls) == 1
        assert len(deliveries.ListDirectives.calls) == 1

    def test_pending_reviews_come_counted(self, client_del):
        """The number that orders the attention box comes out ready — counting it
        in each client is the same rule written three times."""
        prs = client_del.get(
            "/api/v1/pull-requests?demand_id=dem-1", headers=REST_HEADERS
        ).json()
        assert prs[0]["pending_reviews"] == 2

    def test_the_queue_belongs_to_one_repository(self, client_del, deliveries):
        r = client_del.get("/api/v1/repos/repo-1/merge-queue", headers=REST_HEADERS)
        assert r.status_code == 200
        assert r.json()[0]["state"] == "queued"
        assert deliveries.GetMergeQueue.requests[0].repo_id == "repo-1"

    def test_entering_the_queue_carries_an_idempotency_key(self, client_del, deliveries):
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 201
        assert r.json()["position"] == 1
        assert deliveries.EnqueueMerge.requests[0].idempotency_key != ""

    def test_the_directives_payload_crosses_whole(self, client_del):
        """Each directive kind's shape is the techlead's (ADR-0011): the edge
        passes it on, it does not interpret it."""
        d = client_del.get(
            "/api/v1/directives?project_id=prj-1", headers=REST_HEADERS
        ).json()
        assert d[0]["payload"] == {"from": "dem-0", "to": "dem-1"}
        assert d[0]["decided_by_name"] == "Dev"

    def test_a_viewer_does_not_push_the_queue(self, client_del, core):
        viewer_role(core)
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 403

    def test_with_no_active_account_it_is_refused(self, client_del):
        r = client_del.get(
            "/api/v1/repos/repo-1/merge-queue", headers={"authorization": token_for()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_the_delivery_board(self, stub_ent):
        resp = await stub_ent.GetDeliveryBoard(
            bff.GetDeliveryBoardRequest(project_id="prj-1"), metadata=ACCOUNT
        )
        assert [p.id for p in resp.pull_requests] == ["pr-1"]
        assert resp.pull_requests[0].pending_reviews == 2
        assert resp.directives[0].kind == bff.Directive.KIND_CHERRY_PICK

    async def test_entering_the_queue_comes_back_as_an_entry(self, stub_ent):
        resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.HasField("entry")
        assert not resp.HasField("refusal")
        assert resp.entry.state == bff.MergeQueueEntry.STATE_QUEUED

    async def test_the_client_may_send_its_own_idempotency_key(self, stub_ent, deliveries):
        await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(
                repo_id="repo-1", demand_id="dem-1", idempotency_key="minha-key"
            ),
            metadata=ACCOUNT,
        )
        assert deliveries.EnqueueMerge.requests[0].idempotency_key == "minha-key"

    async def test_a_viewer_does_not_decide_a_directive(self, stub_ent, core):
        viewer_role(core)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_ent.DecideDirective(
                bff.DecideDirectiveRequest(directive_id="dir-1"), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_with_no_token_it_is_unauthenticated(self, stub_ent):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_ent.GetMergeQueue(
                bff.GetMergeQueueRequest(repo_id="repo-1"), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestParityBetweenTransports:
    """One function, two adapters — and the proof that it stays that way."""

    async def test_the_board_is_the_same_on_both_ports(self, client_del, stub_ent):
        rest = client_del.get(
            "/api/v1/delivery/board?project_id=prj-1", headers=REST_HEADERS
        ).json()
        grpc_resp = await stub_ent.GetDeliveryBoard(
            bff.GetDeliveryBoardRequest(project_id="prj-1"), metadata=ACCOUNT
        )

        assert [p.id for p in grpc_resp.pull_requests] == [
            p["id"] for p in rest["pull_requests"]
        ]
        # The derived field is the point: if an adapter recomputed it, this is
        # where
        # a diferença apareceria.
        assert [p.pending_reviews for p in grpc_resp.pull_requests] == [
            p["pending_reviews"] for p in rest["pull_requests"]
        ]
        assert [d.id for d in grpc_resp.directives] == [d["id"] for d in rest["directives"]]
