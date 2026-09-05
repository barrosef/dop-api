"""The executor on both transports, against a fake core.

The test that carries this file is the one about DECLARED ISOLATION: a request
with no `min_tier` has to be refused AT THE EDGE, and — what really proves the
rule — the core must not even be called. A BFF that "helps" by filling the gap
chooses the isolation of code that is not its own, and chooses downwards;
whoever asked for a microVM and got a container finds out from the incident.

The second is the one about DESTRUCTION: it is irreversible and takes the
workspace with it, and the contract has to say so — including by returning a
confirmation rather than a mute 204.

The fakes and the fixtures live HERE, and not in `conftest.py`: there are other
agents working in that shared file.
"""

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import timestamp_pb2

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, execution_pb2, identity_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.execution import ExecutionServicer
from app.grpcapi.gen.dop.bff.v1 import execution_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import execution_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import (
    AuthInterceptor,
    ErrorInterceptor,
    LoggingInterceptor,
)
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import execution as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}


def viewer_role(core) -> None:
    """Rebaixa o ator a viewer.

    Like `core.demote_to_developer()`, but for the role that may NOT touch the
    sandbox's life cycle. The role is resolved once, at login, from
    ListMemberships — so demoting means changing what that RPC answers, and not
    a shortcut in the context, which would prove less than it seems.
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


# ── a fake core ────────────────────────────────────────────────────────────


class FakeExecution:
    """Núcleo fake do executor.

    The ACTIVE sandbox has `last_active_at`; the freshly provisioned one does
    NOT — it is the case that tells absent from zeroed, and without it the field
    would always seem
    preenchido.
    """

    def __init__(self):
        self.sandbox = execution_pb2.Sandbox(
            id="sbx-1",
            demand=common_pb2.DemandRef(id="dem-1"),
            state=execution_pb2.Sandbox.STATE_ACTIVE,
            tier=execution_pb2.ISOLATION_TIER_HARDWARE,
            namespace="dop-dem1",
            endpoints=[
                execution_pb2.SandboxEndpoint(
                    name="portal-backend",
                    url="https://portal-backend--dem1.dop.local",
                    port=8080,
                    state="running",
                )
            ],
            last_active_at=timestamp_pb2.Timestamp(seconds=1_770_000_000),
        )
        self.recem_criado = execution_pb2.Sandbox(
            id="sbx-2",
            demand=common_pb2.DemandRef(id="dem-2"),
            state=execution_pb2.Sandbox.STATE_PROVISIONING,
            tier=execution_pb2.ISOLATION_TIER_NAMESPACE,
            namespace="dop-dem2",
        )
        self.ProvisionSandbox = FakeCall(self.sandbox)
        self.DescribeSandbox = FakeCall(self.sandbox)
        self.SuspendSandbox = FakeCall(self.sandbox)
        self.ResumeSandbox = FakeCall(self.sandbox)
        self.DestroySandbox = FakeCall(
            execution_pb2.DestroySandboxResponse(destroyed=True)
        )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def execution(core, monkeypatch):
    fake = FakeExecution()
    monkeypatch.setattr(stubs, "execution_stub", lambda: fake)
    return fake


def _app_with_routes():
    """The BFF's real app, with this domain's routes registered.

    `app/main.py` belongs to the repository's owner and does not include this
    router yet (see the report). Checking before including keeps the test correct
    once the registration lands in `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/sandboxes" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_exec(execution):
    with TestClient(_app_with_routes()) as c:
        yield c


@pytest.fixture
async def stub_exec(execution):
    """A real gRPC server, on an ephemeral port, with this domain's servicer.

    It does not reuse conftest's `grpc_server` because `GrpcServer` does not
    register this servicer yet (the registration is the repository owner's). The
    interceptor stack is the same — it is what makes the token, the context and
    the decorators
    valerem dentro do servicer.
    """
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_ExecutionServiceServicer_to_server(ExecutionServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.ExecutionServiceStub(channel)
    finally:
        await server.stop(0)


# ── testes ──────────────────────────────────────────────────────────────────


class TestIsolationIsDeclaredNeverPresumed:
    def test_rest_with_no_tier_is_a_422_and_the_core_is_not_called(self, client_exec, execution):
        """The refusal is the behaviour; NOT calling the core is the proof.

        If the edge had a default, the core would be called with it and this test
        would start showing a call — which is exactly the symptom nobody would
        notice in a review.
        """
        r = client_exec.post(
            "/api/v1/sandboxes", headers=REST_HEADERS, json={"demand_id": "dem-1"}
        )
        assert r.status_code == 422
        assert execution.ProvisionSandbox.calls == []

    def test_rest_with_an_unknown_tier_is_a_422(self, client_exec, execution):
        r = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "microvm"},
        )
        assert r.status_code == 422
        assert execution.ProvisionSandbox.calls == []

    async def test_grpc_unspecified_is_invalid_argument(self, stub_exec, execution):
        """UNSPECIFIED in protobuf is the absence, and the absence does not become a default."""
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.ProvisionSandbox(
                bff.ProvisionSandboxRequest(demand_id="dem-1"), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert execution.ProvisionSandbox.calls == []

    def test_the_declared_tier_goes_down_as_it_came(self, client_exec, execution):
        """Neither downgraded "because the cluster may not have it", nor promoted."""
        client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "hardware"},
        )
        request = execution.ProvisionSandbox.requests[0]
        assert request.min_tier == execution_pb2.ISOLATION_TIER_HARDWARE
        assert request.idempotency_key != ""

    async def test_the_declared_tier_goes_down_as_it_came_over_grpc(self, stub_exec, execution):
        await stub_exec.ProvisionSandbox(
            bff.ProvisionSandboxRequest(
                demand_id="dem-1",
                min_tier=bff.ISOLATION_TIER_KERNEL_EMULATED,
                idempotency_key="key-do-client",
            ),
            metadata=ACCOUNT,
        )
        request = execution.ProvisionSandbox.requests[0]
        assert request.min_tier == execution_pb2.ISOLATION_TIER_KERNEL_EMULATED
        assert request.idempotency_key == "key-do-client"

    def test_the_response_brings_the_delivered_tier(self, client_exec):
        """The client sees what it GOT, not what it asked for (the execution spec §2)."""
        s = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "namespace"},
        ).json()
        assert s["tier"] == "hardware"

    def test_the_cores_refusal_crosses_as_a_412(self, client_exec, execution):
        """A executor without the requested level = a refusal with a message, not degradation."""
        execution.ProvisionSandbox.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION,
            "this executor does not offer \"hardware\" isolation",
        )
        r = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "hardware"},
        )
        assert r.status_code == 412
        assert "does not offer" in r.json()["detail"]


class TestDestruction:
    def test_it_returns_a_confirmation_instead_of_a_204(self, client_exec):
        """An irreversible act deserves a response the client can show."""
        r = client_exec.delete("/api/v1/sandboxes/sbx-1", headers=REST_HEADERS)
        assert r.status_code == 200
        assert r.json() == {"destroyed": True}

    async def test_grpc_confirms_the_same_way(self, stub_exec):
        resp = await stub_exec.DestroySandbox(
            bff.DestroySandboxRequest(id="sbx-1"), metadata=ACCOUNT
        )
        assert resp.destroyed is True

    def test_suspending_and_destroying_are_different_rpcs(self, client_exec, execution):
        """Suspender preserva o workspace; destruir o leva junto.

        The test exists to lock down the most expensive confusion possible in
        this domain: a `DELETE` that actually suspended (or a `/suspend` that
        destroyed) would pass any test that only looked at the status code.
        """
        client_exec.post("/api/v1/sandboxes/sbx-1/suspend", headers=REST_HEADERS)
        assert len(execution.SuspendSandbox.calls) == 1
        assert execution.DestroySandbox.calls == []

        client_exec.delete("/api/v1/sandboxes/sbx-1", headers=REST_HEADERS)
        assert len(execution.DestroySandbox.calls) == 1
        assert len(execution.SuspendSandbox.calls) == 1

    def test_resuming_a_destroyed_sandbox_crosses_with_the_reason(self, client_exec, execution):
        execution.ResumeSandbox.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION,
            "a destroyed sandbox does not resume — the destruction takes the workspace with it",
        )
        r = client_exec.post(
            "/api/v1/sandboxes/sbx-1/resume", headers=REST_HEADERS
        )
        assert r.status_code == 412
        assert "takes the workspace with it" in r.json()["detail"]


class TestRole:
    def test_a_viewer_does_not_provision(self, client_exec, core, execution):
        viewer_role(core)
        r = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "namespace"},
        )
        assert r.status_code == 403
        assert execution.ProvisionSandbox.calls == []

    def test_a_viewer_does_not_destroy(self, client_exec, core, execution):
        viewer_role(core)
        r = client_exec.delete("/api/v1/sandboxes/sbx-1", headers=REST_HEADERS)
        assert r.status_code == 403
        assert execution.DestroySandbox.calls == []

    def test_a_viewer_sees_the_sandbox(self, client_exec, core):
        """Seeing which isolation the demand runs at is what the spec wants visible."""
        viewer_role(core)
        r = client_exec.get("/api/v1/sandboxes/sbx-1", headers=REST_HEADERS)
        assert r.status_code == 200
        assert r.json()["tier"] == "hardware"

    def test_a_developer_changes_the_life_cycle(self, client_exec, core):
        core.demote_to_developer()
        r = client_exec.post(
            "/api/v1/sandboxes/sbx-1/suspend", headers=REST_HEADERS
        )
        assert r.status_code == 200

    async def test_a_viewer_does_not_provision_over_grpc(self, stub_exec, core):
        viewer_role(core)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.ProvisionSandbox(
                bff.ProvisionSandboxRequest(
                    demand_id="dem-1", min_tier=bff.ISOLATION_TIER_NAMESPACE
                ),
                metadata=ACCOUNT,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED


class TestREST:
    def test_describing_brings_the_endpoints(self, client_exec):
        s = client_exec.get(
            "/api/v1/sandboxes/sbx-1", headers=REST_HEADERS
        ).json()
        assert s["state"] == "active"
        assert s["namespace"] == "dop-dem1"
        assert s["endpoints"][0]["state"] == "running"

    def test_with_no_activity_it_comes_back_null_not_zeroed(self, client_exec, execution):
        """Epoch zero would make the automatic suspension read "idle since 1970"."""
        execution.DescribeSandbox.returns(execution.recem_criado)
        s = client_exec.get(
            "/api/v1/sandboxes/sbx-2", headers=REST_HEADERS
        ).json()
        assert s["last_active_at"] is None
        assert s["state"] == "provisioning"

    def test_with_no_active_account_it_is_refused(self, client_exec):
        """SP-0's rule, applied by the decorator in the USE CASE."""
        r = client_exec.get(
            "/api/v1/sandboxes/sbx-1", headers={"authorization": token_for()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_with_no_token(self, stub_exec):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.DescribeSandbox(
                bff.DescribeSandboxRequest(id="sbx-1"), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_with_no_activity_it_stays_absent(self, stub_exec, execution):
        execution.DescribeSandbox.returns(execution.recem_criado)
        resp = await stub_exec.DescribeSandbox(
            bff.DescribeSandboxRequest(id="sbx-2"), metadata=ACCOUNT
        )
        assert not resp.HasField("last_active_at")

    async def test_a_5xx_detail_from_the_core_does_not_leak(self, stub_exec, execution):
        execution.DescribeSandbox.fails_with(
            grpc.StatusCode.INTERNAL, "pq://user:senha@db:5432 caiu"
        )
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.DescribeSandbox(
                bff.DescribeSandboxRequest(id="sbx-1"), metadata=ACCOUNT
            )
        assert e.value.details() == "internal error"
        assert "senha" not in e.value.details()


class TestParityBetweenTransports:
    """The alarm that fires if anybody reimplements the use case in an adapter."""

    async def test_describing_is_the_same_on_both_ports(self, client_exec, stub_exec):
        rest = client_exec.get(
            "/api/v1/sandboxes/sbx-1", headers=REST_HEADERS
        ).json()
        resp = await stub_exec.DescribeSandbox(
            bff.DescribeSandboxRequest(id="sbx-1"), metadata=ACCOUNT
        )
        assert resp.id == rest["id"]
        assert resp.demand_id == rest["demand_id"]
        assert resp.namespace == rest["namespace"]
        # The edge's enum and the edge's name say the SAME thing about the tier —
        # this is where a divergent conversion table would show up.
        assert bff.IsolationTier.Name(resp.tier) == f"ISOLATION_TIER_{rest['tier'].upper()}"
        assert bff.Sandbox.State.Name(resp.state) == f"STATE_{rest['state'].upper()}"
        for g, j in zip(resp.endpoints, rest["endpoints"], strict=True):
            assert g.name == j["name"]
            assert g.url == j["url"]
            assert g.port == j["port"]
