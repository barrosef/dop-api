"""Substrato de execução nos dois transportes, contra um núcleo fake.

O teste que carrega o arquivo é o do ISOLAMENTO DECLARADO: request sem
`min_tier` tem de ser recusado NA BORDA, e — o que realmente prova a regra — o
núcleo não pode nem chegar a ser chamado. Um BFF que "ajuda" preenchendo o
vazio escolhe o isolamento de um código que não é dele, e escolhe para baixo;
quem pediu microVM e recebeu container descobre pelo incidente.

O segundo é o de DESTRUIÇÃO: ela é irreversível e leva o workspace junto, e o
contrato tem de dizer isso — inclusive devolvendo confirmação em vez de um 204
mudo.

Fakes e fixtures ficam AQUI, e não no `conftest.py`: há outros agentes
escrevendo neste repositório agora.
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

    Como `core.demote_to_developer()`, mas para o role que NÃO pode mexer no
    ciclo de vida do sandbox. O role é resolvido uma vez, no login, a partir de
    ListMemberships — então rebaixar é trocar o que esse RPC responde, e não um
    atalho no context, que provaria menos do que parece.
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


# ── núcleo fake ────────────────────────────────────────────────────────────


class ExecucaoFalsa:
    """Núcleo fake do substrato.

    O sandbox ATIVO tem `last_active_at`; o recém-provisionado NÃO tem — é o
    caso que distingue ausente de zerado, e sem ele o campo pareceria sempre
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
def execucao(core, monkeypatch):
    fake = ExecucaoFalsa()
    monkeypatch.setattr(stubs, "execution_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app real do BFF, com as rotas deste domínio registradas.

    `app/main.py` é do dono do repositório e ainda não inclui este router (ver o
    relatório). A checagem antes de incluir faz o teste continuar correto
    depois que o registro entrar no `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/sandboxes" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_exec(execucao):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_exec(execucao):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    Não reusa `grpc_server` do conftest porque `GrpcServer` ainda não
    registra este servicer (o registro é do dono do repositório). A pilha de
    interceptores é a mesma — é ela que faz token, context e decorators
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
    def test_rest_with_no_tier_is_a_422_and_the_core_is_not_called(self, client_exec, execucao):
        """A recusa é o comportamento; NÃO chamar o núcleo é a prova.

        Se a borda tivesse um default, o núcleo seria chamado com ele e este
        teste passaria a mostrar uma call — que é exatamente o sintoma que
        ninguém repararia numa revisão.
        """
        r = client_exec.post(
            "/api/v1/sandboxes", headers=REST_HEADERS, json={"demand_id": "dem-1"}
        )
        assert r.status_code == 422
        assert execucao.ProvisionSandbox.calls == []

    def test_rest_with_an_unknown_tier_is_a_422(self, client_exec, execucao):
        r = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "microvm"},
        )
        assert r.status_code == 422
        assert execucao.ProvisionSandbox.calls == []

    async def test_grpc_unspecified_is_invalid_argument(self, stub_exec, execucao):
        """UNSPECIFIED no protobuf é a ausência, e a ausência não vira default."""
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.ProvisionSandbox(
                bff.ProvisionSandboxRequest(demand_id="dem-1"), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert execucao.ProvisionSandbox.calls == []

    def test_the_declared_tier_goes_down_as_it_came(self, client_exec, execucao):
        """Nem rebaixado "porque o cluster pode não ter", nem promovido."""
        client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "hardware"},
        )
        request = execucao.ProvisionSandbox.requests[0]
        assert request.min_tier == execution_pb2.ISOLATION_TIER_HARDWARE
        assert request.idempotency_key != ""

    async def test_the_declared_tier_goes_down_as_it_came_over_grpc(self, stub_exec, execucao):
        await stub_exec.ProvisionSandbox(
            bff.ProvisionSandboxRequest(
                demand_id="dem-1",
                min_tier=bff.ISOLATION_TIER_KERNEL_EMULATED,
                idempotency_key="key-do-client",
            ),
            metadata=ACCOUNT,
        )
        request = execucao.ProvisionSandbox.requests[0]
        assert request.min_tier == execution_pb2.ISOLATION_TIER_KERNEL_EMULATED
        assert request.idempotency_key == "key-do-client"

    def test_the_response_brings_the_delivered_tier(self, client_exec):
        """O client vê o que RECEBEU, não o que pediu (spec do substrato §2)."""
        s = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "namespace"},
        ).json()
        assert s["tier"] == "hardware"

    def test_the_cores_refusal_crosses_as_a_412(self, client_exec, execucao):
        """Substrato sem o nível request = recusa com mensagem, não degradação."""
        execucao.ProvisionSandbox.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION,
            "este substrato não oferece isolamento \"hardware\"",
        )
        r = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "hardware"},
        )
        assert r.status_code == 412
        assert "não oferece isolamento" in r.json()["detail"]


class TestDestruction:
    def test_it_returns_a_confirmation_instead_of_a_204(self, client_exec):
        """Ato irreversível merece response que o client possa mostrar."""
        r = client_exec.delete("/api/v1/sandboxes/sbx-1", headers=REST_HEADERS)
        assert r.status_code == 200
        assert r.json() == {"destroyed": True}

    async def test_grpc_confirms_the_same_way(self, stub_exec):
        resp = await stub_exec.DestroySandbox(
            bff.DestroySandboxRequest(id="sbx-1"), metadata=ACCOUNT
        )
        assert resp.destroyed is True

    def test_suspending_and_destroying_are_different_rpcs(self, client_exec, execucao):
        """Suspender preserva o workspace; destruir o leva junto.

        O teste existe para travar a confusão mais cara possível neste domínio:
        um `DELETE` que na verdade suspendesse (ou um `/suspend` que destruísse)
        passaria em qualquer teste que só olhasse o código de status.
        """
        client_exec.post("/api/v1/sandboxes/sbx-1/suspend", headers=REST_HEADERS)
        assert len(execucao.SuspendSandbox.calls) == 1
        assert execucao.DestroySandbox.calls == []

        client_exec.delete("/api/v1/sandboxes/sbx-1", headers=REST_HEADERS)
        assert len(execucao.DestroySandbox.calls) == 1
        assert len(execucao.SuspendSandbox.calls) == 1

    def test_resuming_a_destroyed_sandbox_crosses_with_the_reason(self, client_exec, execucao):
        execucao.ResumeSandbox.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION,
            "sandbox destruído não retoma — a destruição leva o workspace junto",
        )
        r = client_exec.post(
            "/api/v1/sandboxes/sbx-1/resume", headers=REST_HEADERS
        )
        assert r.status_code == 412
        assert "leva o workspace junto" in r.json()["detail"]


class TestRole:
    def test_a_viewer_does_not_provision(self, client_exec, core, execucao):
        viewer_role(core)
        r = client_exec.post(
            "/api/v1/sandboxes",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1", "min_tier": "namespace"},
        )
        assert r.status_code == 403
        assert execucao.ProvisionSandbox.calls == []

    def test_a_viewer_does_not_destroy(self, client_exec, core, execucao):
        viewer_role(core)
        r = client_exec.delete("/api/v1/sandboxes/sbx-1", headers=REST_HEADERS)
        assert r.status_code == 403
        assert execucao.DestroySandbox.calls == []

    def test_a_viewer_sees_the_sandbox(self, client_exec, core):
        """Ver em que isolamento a demand roda é o que a spec quer visível."""
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

    def test_with_no_activity_it_comes_back_null_not_zeroed(self, client_exec, execucao):
        """Época zero faria a suspensão automática ler "ocioso desde 1970"."""
        execucao.DescribeSandbox.returns(execucao.recem_criado)
        s = client_exec.get(
            "/api/v1/sandboxes/sbx-2", headers=REST_HEADERS
        ).json()
        assert s["last_active_at"] is None
        assert s["state"] == "provisioning"

    def test_with_no_active_account_it_is_refused(self, client_exec):
        """Regra do SP-0, aplicada pelo decorator no CASO DE USO."""
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

    async def test_with_no_activity_it_stays_absent(self, stub_exec, execucao):
        execucao.DescribeSandbox.returns(execucao.recem_criado)
        resp = await stub_exec.DescribeSandbox(
            bff.DescribeSandboxRequest(id="sbx-2"), metadata=ACCOUNT
        )
        assert not resp.HasField("last_active_at")

    async def test_a_5xx_detail_from_the_core_does_not_leak(self, stub_exec, execucao):
        execucao.DescribeSandbox.fails_with(
            grpc.StatusCode.INTERNAL, "pq://user:senha@db:5432 caiu"
        )
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.DescribeSandbox(
                bff.DescribeSandboxRequest(id="sbx-1"), metadata=ACCOUNT
            )
        assert e.value.details() == "internal error"
        assert "senha" not in e.value.details()


class TestParityBetweenTransports:
    """O alarme que dispara se alguém reimplementar o caso de uso num adaptador."""

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
        # O enum da borda e o name da borda dizem a MESMA coisa sobre o tier —
        # é aqui que uma tabela de conversão divergente apareceria.
        assert bff.IsolationTier.Name(resp.tier) == f"ISOLATION_TIER_{rest['tier'].upper()}"
        assert bff.Sandbox.State.Name(resp.state) == f"STATE_{rest['state'].upper()}"
        for g, j in zip(resp.endpoints, rest["endpoints"], strict=True):
            assert g.name == j["name"]
            assert g.url == j["url"]
            assert g.port == j["port"]
