"""The demand on both transports, against a fake core.

The test that matters most here is the parity one: it is what stops anybody from
reimplementing a use case in the servicer (or in the router) with nobody
noticing in review. The others protect the two things the edge ADDS — the
derived fields of "where the demand is" and the cockpit in one call — and the
absent ≠ zeroed discipline, which shows up here in a stage's date and in an
agent's brief.

The doubles and the fixtures live in THIS file, and not in conftest: there are
other agents writing in this repository, and conftest is shared territory. What
already exists there (the token, the metadata, FakeCall, the identity core) is
imported.
"""

from datetime import datetime

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, demand_pb2, identity_pb2, workflow_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.demand import DemandServicer
from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import demand_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import demand as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}


def viewer_role(core) -> None:
    """Rebaixa o ator a viewer.

    The role is resolved ONCE, at login, from ListMemberships — so demoting
    means changing what that RPC answers, not a shortcut in the context. Viewer
    (and not developer) because in the work cycle a developer WRITES: it is
    ele quem toca a demand.
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


class DemandasFalsas:
    """Núcleo fake de demand.

    The demand has three stages on purpose: one finished, one running with a
    human gate (the one awaiting a decision) and one that has not even started —
    with no date at all, which is the case where absent and zeroed get confused.
    The threads are two as well: one with an agent brief and one WITHOUT.
    """

    def __init__(self):
        d = demand_pb2.Demand(
            id="dem-1",
            project=common_pb2.ProjectRef(id="prj-1"),
            external_key="SUOPT-1315",
            title="Corrigir o cálculo de rateio",
            card_type="bug",
            provider_status="In Progress",
            dop_status=demand_pb2.DOP_STATUS_DOING,
            flow_id="flow-1",
            flow_version=2,
        )
        context = d.stages.add(
            key="context",
            name="Contexto",
            type=workflow_pb2.STAGE_TYPE_CONTEXT,
            status=demand_pb2.STAGE_STATUS_DONE,
            gate=workflow_pb2.GATE_NONE,
        )
        context.started_at.FromDatetime(datetime(2026, 8, 25, 9, 0))
        context.finished_at.FromDatetime(datetime(2026, 8, 25, 9, 30))
        context.artifacts.add(
            id="art-1",
            kind=workflow_pb2.ARTIFACT_KIND_DOCUMENT,
            name="context.md",
            object_ref="obj://ctx",
            version=1,
        )

        spec = d.stages.add(
            key="spec",
            name="Spec",
            type=workflow_pb2.STAGE_TYPE_SPEC,
            status=demand_pb2.STAGE_STATUS_RUNNING,
            gate=workflow_pb2.GATE_HUMAN,
        )
        spec.started_at.FromDatetime(datetime(2026, 8, 25, 10, 0))
        # finished_at stays ABSENT: the stage did not finish.

        # It has not even started: no start and no end.
        d.stages.add(
            key="implementacao",
            name="Implementation",
            type=workflow_pb2.STAGE_TYPE_IMPLEMENTATION,
            status=demand_pb2.STAGE_STATUS_PENDING,
            gate=workflow_pb2.GATE_NONE,
        )
        self.demand = d

        main = demand_pb2.Thread(
            id="th-1", demand=common_pb2.DemandRef(id="dem-1"), key="main"
        )
        main.card.CopyFrom(
            demand_pb2.AgentCard(
                purpose="tocar a demand",
                tools=["mcp:mysql"],
                model="opus",
                effort="high",
                budget_micros=5_000_000,
            )
        )
        # A thread with NO brief: it is the case that tells absent from zeroed.
        without_card = demand_pb2.Thread(
            id="th-2",
            demand=common_pb2.DemandRef(id="dem-1"),
            key="db-forensics",
            blocked=True,
        )
        self.thread, self.thread_without_card = main, without_card

        self.ListDemands = FakeCall(
            demand_pb2.ListDemandsResponse(
                demands=[d], page=common_pb2.PageResponse(next_token="pag-2", total=1)
            )
        )
        self.GetDemand = FakeCall(d)
        self.StartDemand = FakeCall(d)
        self.AdvanceStage = FakeCall(d.stages[2])
        self.DecideGate = FakeCall(d.stages[1])
        self.ListThreads = FakeCall(
            demand_pb2.ListThreadsResponse(threads=[main, without_card])
        )
        self.CreateThread = FakeCall(main)
        self.PostMessage = FakeCall(
            demand_pb2.Message(
                id="msg-1",
                thread_id="th-1",
                author=common_pb2.ActorRef(
                    kind=common_pb2.ActorRef.KIND_USER, id="u-1", name="Dev"
                ),
                text="you may proceed",
            )
        )
        finding = demand_pb2.Finding(
            id="fnd-1", thread_id="th-2", title="a missing index on requests"
        )
        finding.payload.update({"tabela": "requests", "linhas": 4200})
        self.finding = finding
        self.PublishFinding = FakeCall(finding)
        # The read the core came to expose (P-19). Before it, the cockpit
        # returned an empty list with a flag saying "there is no way to know".
        self.ListFindings = FakeCall(
            demand_pb2.ListFindingsResponse(findings=[finding])
        )


@pytest.fixture
def demands(core, monkeypatch):
    fake = DemandasFalsas()
    monkeypatch.setattr(stubs, "demand_stub", lambda: fake)
    return fake


def _app_with_routes():
    """The app with the demand routes.

    `app/main.py` is not this agent's: until the registration lands there, the
    test assembles the app and adds the router. The `if` keeps the test valid
    after the registration, with no duplicated route.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/demands" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_dem(demands):
    with TestClient(_app_with_routes()) as c:
        yield c


@pytest.fixture
async def stub_dem(demands):
    """A real gRPC server, on an ephemeral port, with the SAME interceptors as
    the production port — it is what proves the ContextVar survives as far as the
    servicer.

    Its own (and not conftest's `grpc_server`) because `app/grpcapi/server.py`
    does not register this servicer yet, and that file is not this agent's.
    """
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_DemandServiceServicer_to_server(DemandServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.DemandServiceStub(channel)
    finally:
        await server.stop(0)


class TestREST:
    def test_the_cockpit_brings_demand_threads_and_findings_in_one_response(
        self, client_dem, demands
    ):
        r = client_dem.get("/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["demand"]["external_key"] == "SUOPT-1315"
        assert [t["key"] for t in body["threads"]] == ["main", "db-forensics"]
        assert [f["title"] for f in body["findings"]] == ["a missing index on requests"]
        assert body["findings"][0]["payload"] == {"tabela": "requests", "linhas": 4200}
        # THREE calls to the core, one response to the client.
        assert len(demands.GetDemand.calls) == 1
        assert len(demands.ListThreads.calls) == 1
        assert len(demands.ListFindings.calls) == 1
        assert demands.ListFindings.requests[0].demand_id == "dem-1"
        # With no thread: the board is the whole demand's.
        assert demands.ListFindings.requests[0].thread_id == ""

    def test_an_empty_list_of_findings_now_means_an_empty_list(
        self, client_dem, demands
    ):
        """O fim de `findings_available`.

        It existed because the core's contract had no way to read findings: the
        list came back empty and the flag separated "it has no findings" from
        "there is no way to know". With `ListFindings` only the first case is
        left — and a constant flag would be noise somebody one day reads
        backwards.
        """
        demands.ListFindings.returns(demand_pb2.ListFindingsResponse())
        body = client_dem.get(
            "/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS
        ).json()
        assert body["findings"] == []
        assert "findings_available" not in body

    def test_a_failure_reading_findings_brings_the_whole_cockpit_down(
        self, client_dem, demands
    ):
        """A half response that does not say it is half is worse than an error.

        It is the other side of the same decision: with no flag to say "it did
        not work", whoever cannot read the findings returns the core's status,
        and not a cockpit that looks complete with a piece missing.
        """
        demands.ListFindings.fails_with(grpc.StatusCode.PERMISSION_DENIED)
        r = client_dem.get("/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS)
        assert r.status_code == 403

    def test_a_stage_that_has_not_started_comes_back_null_not_zeroed(self, client_dem):
        """Absent and zeroed are different things.

        A zeroed start would become 1 January 1970 on the screen — and a wrong
        date is worse than no date at all.
        """
        d = client_dem.get("/api/v1/demands/dem-1", headers=REST_HEADERS).json()
        context, spec, impl = d["stages"]
        assert context["started_at"] is not None
        assert context["finished_at"] is not None
        assert spec["started_at"] is not None
        assert spec["finished_at"] is None
        assert impl["started_at"] is None

    def test_the_derived_fields_say_where_the_demand_is(self, client_dem):
        """The first stage that is not finished, and the one waiting for a person.

        It is the reading the cockpit, dop-cli and the agent would each do their
        own way — and it is how the list and the screen start disagreeing.
        """
        d = client_dem.get("/api/v1/demands/dem-1", headers=REST_HEADERS).json()
        assert d["current_stage_key"] == "spec"
        assert d["awaiting_decision"] is True
        assert d["blocked"] is False
        # The stage that waits is the human gate's, and only it.
        assert [s["awaiting_decision"] for s in d["stages"]] == [False, True, False]

    def test_a_thread_with_no_brief_comes_back_null(self, client_dem):
        threads = client_dem.get(
            "/api/v1/demands/dem-1/threads", headers=REST_HEADERS
        ).json()
        with_card, without_card = threads
        assert with_card["card"]["model"] == "opus"
        assert without_card["card"] is None

    def test_the_list_passes_on_the_next_page_token(self, client_dem):
        r = client_dem.get("/api/v1/demands", headers=REST_HEADERS)
        assert r.json()["next_page_token"] == "pag-2"

    def test_a_viewer_does_not_start_a_demand(self, client_dem, core):
        viewer_role(core)
        r = client_dem.post(
            "/api/v1/demands",
            headers=REST_HEADERS,
            json={"project_id": "prj-1", "external_key": "SUOPT-1315"},
        )
        assert r.status_code == 403

    def test_a_write_carries_an_idempotency_key(self, client_dem, demands):
        """With no key, the channel's retry opens two demands for the same card."""
        client_dem.post(
            "/api/v1/demands",
            headers=REST_HEADERS,
            json={"project_id": "prj-1", "external_key": "SUOPT-1315"},
        )
        assert demands.StartDemand.requests[0].idempotency_key != ""

    def test_an_invented_stage_status_is_refused(self, client_dem):
        """The vocabulary is closed, and the refusal lives in the use case — that
        is why it holds the same at both ports."""
        r = client_dem.post(
            "/api/v1/demands/dem-1/stages/spec/advance",
            headers=REST_HEADERS,
            json={"status": "quase-la"},
        )
        assert r.status_code == 422

    def test_with_no_active_account_it_is_refused(self, client_dem):
        r = client_dem.get(
            "/api/v1/demands/dem-1", headers={"authorization": token_for()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_cockpit(self, stub_dem):
        resp = await stub_dem.GetDemandCockpit(
            bff.GetDemandCockpitRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.demand.external_key == "SUOPT-1315"
        assert [t.key for t in resp.threads] == ["main", "db-forensics"]
        assert [f.title for f in resp.findings] == ["a missing index on requests"]

    async def test_the_findings_available_field_no_longer_exists(self, stub_dem):
        """Removed from the edge's contract, with number 4 reserved.

        Reserving stops a new field from inheriting the flag's number and being
        read by an old client as if it still were it — in silence.
        """
        fields = bff.DemandCockpit.DESCRIPTOR.fields_by_name
        assert "findings_available" not in fields
        assert all(f.number != 4 for f in fields.values())

    async def test_a_stage_with_no_date_has_no_field(self, stub_dem):
        d = await stub_dem.GetDemand(bff.GetDemandRequest(id="dem-1"), metadata=ACCOUNT)
        context, spec, impl = d.stages
        assert context.HasField("started_at") and context.HasField("finished_at")
        assert spec.HasField("started_at")
        assert not spec.HasField("finished_at")
        assert not impl.HasField("started_at")

    async def test_a_thread_with_no_brief_has_no_field(self, stub_dem):
        resp = await stub_dem.ListThreads(
            bff.ListThreadsRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        with_card, without_card = resp.threads
        assert with_card.HasField("card")
        assert not without_card.HasField("card")

    async def test_a_thread_created_with_no_brief_sends_no_brief_to_the_core(
        self, stub_dem, demands
    ):
        """A zeroed card would declare an agent with no purpose and no budget."""
        await stub_dem.CreateThread(
            bff.CreateThreadRequest(demand_id="dem-1", key="logs"), metadata=ACCOUNT
        )
        assert not demands.CreateThread.requests[0].HasField("card")

    async def test_the_client_may_send_its_own_idempotency_key(self, stub_dem, demands):
        """Over gRPC the one that knows it is retrying is the client; REST has
        nowhere to carry the key and gets one of ours."""
        await stub_dem.StartDemand(
            bff.StartDemandRequest(
                project_id="prj-1", external_key="S-1", idempotency_key="minha-key"
            ),
            metadata=ACCOUNT,
        )
        assert demands.StartDemand.requests[0].idempotency_key == "minha-key"

    async def test_a_viewer_does_not_decide_a_gate(self, stub_dem, core):
        viewer_role(core)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_dem.DecideGate(
                bff.DecideGateRequest(demand_id="dem-1", stage_key="spec", approved=True),
                metadata=ACCOUNT,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_with_no_token_it_is_unauthenticated(self, stub_dem):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_dem.GetDemand(
                bff.GetDemandRequest(id="dem-1"), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestParityBetweenTransports:
    """One function, two adapters — and the proof that it stays that way."""

    async def test_the_cockpit_is_the_same_on_both_ports(self, client_dem, stub_dem):
        rest = client_dem.get(
            "/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS
        ).json()
        grpc_resp = await stub_dem.GetDemandCockpit(
            bff.GetDemandCockpitRequest(demand_id="dem-1"), metadata=ACCOUNT
        )

        assert grpc_resp.demand.id == rest["demand"]["id"]
        # The DERIVED fields are the point: if an adapter recomputed them, this
        # is where the difference would show up.
        assert grpc_resp.demand.current_stage_key == rest["demand"]["current_stage_key"]
        assert grpc_resp.demand.awaiting_decision == rest["demand"]["awaiting_decision"]
        assert grpc_resp.demand.blocked == rest["demand"]["blocked"]
        assert [t.key for t in grpc_resp.threads] == [t["key"] for t in rest["threads"]]
        assert [f.id for f in grpc_resp.findings] == [f["id"] for f in rest["findings"]]
        assert dict(grpc_resp.findings[0].payload) == rest["findings"][0]["payload"]

        # And what is optional, which is where absent≠zeroed may diverge between
        # the ends.
        for e_grpc, e_rest in zip(grpc_resp.demand.stages, rest["demand"]["stages"], strict=True):
            assert e_grpc.HasField("started_at") == (e_rest["started_at"] is not None)
            assert e_grpc.HasField("finished_at") == (e_rest["finished_at"] is not None)
        for t_grpc, t_rest in zip(grpc_resp.threads, rest["threads"], strict=True):
            assert t_grpc.HasField("card") == (t_rest["card"] is not None)
