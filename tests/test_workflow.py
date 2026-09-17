"""The workflow on both transports, against a fake core.

Two groups of tests carry the weight:

- **parity** REST × gRPC, which stops anybody from reimplementing a use case in
  an adapter with nobody noticing in review;
- **provenance**, which is what the edge delivers here: where each stage came
  from, read from the core's `contributors`/`origins` FIELDS. One of the tests
  exists precisely to prove the edge NO LONGER depends on the shape of the
  `resolved_from` sentence — it is passed on whole, and nothing more.

The doubles and the fixtures live in THIS file (conftest is shared territory);
what already exists there is imported.
"""

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import workflow_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.grpcapi.workflow import WorkflowServicer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import workflow as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# The exact sentence the core writes (renderTrail, in
# internal/domain/workflow/entity.go): the chain from the most specific to the
# most generic, and the per-stage detail when the levels diverge.
TRAIL = "account ◂ platform — stages: context (platform), spec (account)"


def _flow(**fields) -> workflow_pb2.Flow:
    f = workflow_pb2.Flow(
        id="flow-1",
        name="the account's flow",
        description="ACME's composition",
        version=3,
        owner_scope="account",
        owner_id="acct-1",
        **fields,
    )
    f.stages.add(
        key="context",
        name="Contexto",
        type=workflow_pb2.STAGE_TYPE_CONTEXT,
        artifacts=[workflow_pb2.ARTIFACT_KIND_DOCUMENT],
        gate=workflow_pb2.GATE_NONE,
    )
    f.stages.add(
        key="spec",
        name="Spec",
        type=workflow_pb2.STAGE_TYPE_SPEC,
        artifacts=[workflow_pb2.ARTIFACT_KIND_SPEC],
        gate=workflow_pb2.GATE_HUMAN,
        subtypes=["aaa"],
    )
    return f


class FakeFlows:
    """The flow's fake core. It records the request and returns what it is told to."""

    def __init__(self):
        self.flow = _flow()
        self.ListFlows = FakeCall(workflow_pb2.ListFlowsResponse(flows=[self.flow]))
        self.GetFlow = FakeCall(self.flow)
        self.CreateFlow = FakeCall(self.flow)
        self.UpdateFlow = FakeCall(self.flow)
        self.PromoteFlow = FakeCall(self.flow)
        self.ValidateFlow = FakeCall(
            workflow_pb2.ValidateFlowResponse(
                valid=False,
                errors=["stage 'spec' repeated"],
                warnings=["a flow with no test stage"],
            )
        )
        self.ResolveFlow = FakeCall(self.effective())

    def effective(self, **fields) -> workflow_pb2.EffectiveFlow:
        """The effective flow as the core returns it: the fields AND the sentence.

        Both together because that is how it answers — and it is the only way
        for the test to prove the edge reads the FIELDS: if the double sent only
        a frase, ler dela passaria despercebido.
        """
        padrao = {
            "flow": self.flow,
            "resolved_from": TRAIL,
            "contributors": [
                workflow_pb2.ScopeRef(scope="account", id="acct-1"),
                workflow_pb2.ScopeRef(scope="platform"),
            ],
            "origins": [
                workflow_pb2.StageOrigin(key="context", scope="platform"),
                workflow_pb2.StageOrigin(key="spec", scope="account", scope_id="acct-1"),
            ],
        }
        return workflow_pb2.EffectiveFlow(**{**padrao, **fields})


@pytest.fixture
def flows(core, monkeypatch):
    fake = FakeFlows()
    monkeypatch.setattr(stubs, "workflow_stub", lambda: fake)
    return fake


def _app_with_routes():
    """The app with the flow routes.

    `app/main.py` is not this agent's: until the registration lands there, the
    test assembles the app and adds the router. The `if` keeps the test valid
    after the registration, with no duplicated route.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/flows" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_flow(flows):
    with TestClient(_app_with_routes()) as c:
        yield c


@pytest.fixture
async def stub_flow(flows):
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
    bff_grpc.add_WorkflowServiceServicer_to_server(WorkflowServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.WorkflowServiceStub(channel)
    finally:
        await server.stop(0)


class TestProvenance:
    """Where each stage came from — read from the fields, not from the sentence."""

    def test_the_chain_and_the_origin_per_stage(self, client_flow):
        eff = client_flow.get(
            "/api/v1/flows/effective?scope=project&scope_id=prj-1",
            headers=REST_HEADERS,
        ).json()
        p = eff["provenance"]
        # Do mais específico ao mais genérico, no MESMO vocabulário de
        # owner_scope — which is what the core already sends in the field.
        assert p["contributors"] == ["account", "platform"]
        assert p["origins"] == [
            {"stage_key": "context", "scope": "platform"},
            {"stage_key": "spec", "scope": "account"},
        ]
        # The origins follow the stages' order: origins[i] is stages[i]'s origin.
        assert [o["stage_key"] for o in p["origins"]] == [
            s["key"] for s in eff["flow"]["stages"]
        ]
        assert p["truncated"] is False

    def test_the_edge_does_not_depend_on_the_sentence_shape(self, client_flow, flows):
        """The test that exists to prove the parser is DEAD.

        The sentence arrives unrecognizable — different punctuation, another
        language, without the separators and without the parentheses the old
        parser looked for. The provenance has to come out identical all the same,
        because it comes from the fields. If anybody reintroduces reading the
        sentence, this is where it breaks.
        """
        flows.ResolveFlow.returns(
            flows.effective(resolved_from="resolved from account, then platform")
        )
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p["contributors"] == ["account", "platform"]
        assert [(o["stage_key"], o["scope"]) for o in p["origins"]] == [
            ("context", "platform"),
            ("spec", "account"),
        ]
        # And the sentence, whatever it is, crosses whole and uninterpreted.
        assert p["sentence"] == "resolved from account, then platform"

    def test_the_original_sentence_is_not_lost(self, client_flow):
        """Whoever only wants to print keeps printing — and whoever distrusts the
        structured reading has something to check it against."""
        eff = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()
        assert eff["provenance"]["sentence"] == TRAIL

    def test_a_single_level_also_has_an_origin_per_stage(self, client_flow, flows):
        """When only one level declared, the SENTENCE carries no per-stage detail
        — there is no divergence to explain. The fields carry it.

        It is the difference P-20 bought: before, the edge read the sentence and
        concluded "there are no origins"; now it answers where each stage came
        from even in the case the core did not think worth writing out.
        """
        flows.ResolveFlow.returns(
            flows.effective(
                resolved_from="project",
                contributors=[workflow_pb2.ScopeRef(scope="project", id="prj-1")],
                origins=[
                    workflow_pb2.StageOrigin(key="context", scope="project", scope_id="prj-1"),
                    workflow_pb2.StageOrigin(key="spec", scope="project", scope_id="prj-1"),
                ],
            )
        )
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p["contributors"] == ["project"]
        assert [o["scope"] for o in p["origins"]] == ["project", "project"]
        assert p["truncated"] is False

    def test_a_stage_with_no_reported_origin_marks_truncated(self, client_flow, flows):
        """`truncated` is MEASURED: origins that do not cover the stages.

        It used to be deduced from the "…" with which the core cuts the sentence
        at 12 stages. The cut is of the sentence; the structured list comes
        whole. Measuring keeps the field correct if the core starts cutting the
        list too — and
        what it promises the client is the same: there is a stage whose origin
        nobody can report, which is different from it having no origin.
        """
        flows.ResolveFlow.returns(
            flows.effective(
                origins=[workflow_pb2.StageOrigin(key="context", scope="platform")]
            )
        )
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p["truncated"] is True
        assert [o["stage_key"] for o in p["origins"]] == ["context"]

    def test_with_no_provenance_no_provenance_is_invented(self, client_flow, flows):
        """A silent core: nothing is deduced, and `truncated` does not become a fake alarm.

        With no flow there is no stage to explain — so no origin is missing.
        """
        flows.ResolveFlow.returns(workflow_pb2.EffectiveFlow())
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p == {"contributors": [], "origins": [], "sentence": "", "truncated": False}


class TestREST:
    def test_the_flow_translates_the_stage_vocabulary(self, client_flow):
        f = client_flow.get("/api/v1/flows/flow-1", headers=REST_HEADERS).json()
        assert [s["type"] for s in f["stages"]] == ["context", "spec"]
        assert [s["gate"] for s in f["stages"]] == ["none", "human"]
        assert f["stages"][1]["artifacts"] == ["spec"]

    def test_validation_is_a_report_not_an_error(self, client_flow):
        """The screen needs the list to mark the stages; a 4xx would only give a toast."""
        r = client_flow.post(
            "/api/v1/flows/validate",
            headers=REST_HEADERS,
            json={"name": "f", "owner_scope": "project", "stages": []},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is False
        assert r.json()["warnings"] == ["a flow with no test stage"]

    def test_effective_is_not_captured_as_a_flow_id(self, client_flow, flows):
        """The /flows/effective route comes before /flows/{id} — if the order is
        inverted, this test falls."""
        client_flow.get("/api/v1/flows/effective?scope=account", headers=REST_HEADERS)
        assert flows.ResolveFlow.calls
        assert not flows.GetFlow.calls

    def test_creating_a_flow_carries_an_idempotency_key(self, client_flow, flows):
        client_flow.post(
            "/api/v1/flows",
            headers=REST_HEADERS,
            json={
                "name": "new",
                "owner_scope": "project",
                "owner_id": "prj-1",
                "stages": [{"key": "spec", "type": "spec", "gate": "human"}],
            },
        )
        request = flows.CreateFlow.requests[0]
        assert request.idempotency_key != ""
        # And the vocabulary becomes an enum again on the way out to the core.
        assert request.flow.stages[0].type == workflow_pb2.STAGE_TYPE_SPEC
        assert request.flow.stages[0].gate == workflow_pb2.GATE_HUMAN

    def test_a_developer_composes_a_flow(self, client_flow, core):
        """A flow is knowledge, open within the account (ADR-0010 §6)."""
        core.demote_to_developer()
        r = client_flow.post(
            "/api/v1/flows",
            headers=REST_HEADERS,
            json={"name": "new", "owner_scope": "project", "owner_id": "prj-1"},
        )
        assert r.status_code == 201

    def test_a_developer_does_not_promote_a_flow(self, client_flow, core):
        """Promoting changes the way of working of people who did not ask — it
        requires management."""
        core.demote_to_developer()
        r = client_flow.post(
            "/api/v1/flows/flow-1/promotion",
            headers=REST_HEADERS,
            json={"target_scope": "account", "target_id": "acct-1"},
        )
        assert r.status_code == 403

    def test_with_no_active_account_it_is_refused(self, client_flow):
        r = client_flow.get(
            "/api/v1/flows", headers={"authorization": token_for()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_resolve_returns_a_structured_provenance(self, stub_flow):
        eff = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="project", scope_id="prj-1"), metadata=ACCOUNT
        )
        assert list(eff.provenance.contributors) == ["account", "platform"]
        assert [(o.stage_key, o.scope) for o in eff.provenance.origins] == [
            ("context", "platform"),
            ("spec", "account"),
        ]
        assert eff.provenance.sentence == TRAIL

    async def test_an_absent_flow_is_not_an_empty_flow(self, stub_flow, flows):
        """No level declared a flow: the field does not come. A zeroed flow would
        say a flow exists with no name and no stages."""
        flows.ResolveFlow.returns(workflow_pb2.EffectiveFlow(resolved_from=""))
        eff = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="account"), metadata=ACCOUNT
        )
        assert not eff.HasField("flow")

    async def test_a_developer_does_not_promote_a_flow(self, stub_flow, core):
        core.demote_to_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.PromoteFlow(
                bff.PromoteFlowRequest(flow_id="flow-1", target_scope="account"),
                metadata=ACCOUNT,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_with_no_token_it_is_unauthenticated(self, stub_flow):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.ListFlows(
                bff.ListFlowsRequest(), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_a_core_error_crosses_with_its_own_status(self, stub_flow, flows):
        flows.GetFlow.fails_with(grpc.StatusCode.NOT_FOUND, "flow not found")
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.GetFlow(bff.GetFlowRequest(id="sumiu"), metadata=ACCOUNT)
        assert e.value.code() == grpc.StatusCode.NOT_FOUND


class TestParityBetweenTransports:
    """One function, two adapters — and the proof that it stays that way."""

    async def test_the_effective_flow_is_the_same_on_both_ports(self, client_flow, stub_flow):
        rest = client_flow.get(
            "/api/v1/flows/effective?scope=project&scope_id=prj-1",
            headers=REST_HEADERS,
        ).json()
        grpc_resp = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="project", scope_id="prj-1"), metadata=ACCOUNT
        )

        assert grpc_resp.flow.id == rest["flow"]["id"]
        assert [s.key for s in grpc_resp.flow.stages] == [
            s["key"] for s in rest["flow"]["stages"]
        ]
        # The provenance is what the edge builds — if an adapter rebuilt it,
        # the difference would show up here.
        p_grpc, p_rest = grpc_resp.provenance, rest["provenance"]
        assert list(p_grpc.contributors) == p_rest["contributors"]
        assert [(o.stage_key, o.scope) for o in p_grpc.origins] == [
            (o["stage_key"], o["scope"]) for o in p_rest["origins"]
        ]
        assert p_grpc.sentence == p_rest["sentence"]
        assert p_grpc.truncated == p_rest["truncated"]
