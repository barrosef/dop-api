"""Substrato de execução nos dois transportes, contra um núcleo falso.

O teste que carrega o arquivo é o do ISOLAMENTO DECLARADO: pedido sem
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
from tests.conftest import PROJECT, ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}


def papel_viewer(nucleo) -> None:
    """Rebaixa o ator a viewer.

    Como `nucleo.papel_developer()`, mas para o papel que NÃO pode mexer no
    ciclo de vida do sandbox. O papel é resolvido uma vez, no login, a partir de
    ListMemberships — então rebaixar é trocar o que esse RPC responde, e não um
    atalho no contexto, que provaria menos do que parece.
    """
    nucleo.ListMemberships = ChamadaFalsa(
        identity_pb2.ListMembershipsResponse(
            memberships=[
                identity_pb2.Membership(
                    id="m-1",
                    user=common_pb2.UserRef(id=nucleo.user_id),
                    account=common_pb2.AccountRef(id=nucleo.conta.id),
                    role=identity_pb2.ROLE_VIEWER,
                )
            ]
        )
    )


# ── núcleo falso ────────────────────────────────────────────────────────────


class ExecucaoFalsa:
    """Núcleo falso do substrato.

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
        self.ProvisionSandbox = ChamadaFalsa(self.sandbox)
        self.DescribeSandbox = ChamadaFalsa(self.sandbox)
        self.SuspendSandbox = ChamadaFalsa(self.sandbox)
        self.ResumeSandbox = ChamadaFalsa(self.sandbox)
        self.DestroySandbox = ChamadaFalsa(
            execution_pb2.DestroySandboxResponse(destroyed=True)
        )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def execucao(nucleo, monkeypatch):
    falso = ExecucaoFalsa()
    monkeypatch.setattr(stubs, "execution_stub", lambda: falso)
    return falso


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
def cliente_exec(execucao):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_exec(execucao):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    Não reusa `servidor_grpc` do conftest porque `GrpcServer` ainda não
    registra este servicer (o registro é do dono do repositório). A pilha de
    interceptores é a mesma — é ela que faz token, contexto e decorators
    valerem dentro do servicer.
    """
    servidor = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_ExecutionServiceServicer_to_server(ExecutionServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.ExecutionServiceStub(canal)
    finally:
        await servidor.stop(0)


# ── testes ──────────────────────────────────────────────────────────────────


class TestIsolamentoDeclaradoNuncaPresumido:
    def test_rest_sem_tier_e_422_e_o_nucleo_nao_e_chamado(self, cliente_exec, execucao):
        """A recusa é o comportamento; NÃO chamar o núcleo é a prova.

        Se a borda tivesse um default, o núcleo seria chamado com ele e este
        teste passaria a mostrar uma chamada — que é exatamente o sintoma que
        ninguém repararia numa revisão.
        """
        r = cliente_exec.post(
            "/api/v1/sandboxes", headers=CABECALHOS_REST, json={"demand_id": "dem-1"}
        )
        assert r.status_code == 422
        assert execucao.ProvisionSandbox.chamadas == []

    def test_rest_tier_desconhecido_e_422(self, cliente_exec, execucao):
        r = cliente_exec.post(
            "/api/v1/sandboxes",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1", "min_tier": "microvm"},
        )
        assert r.status_code == 422
        assert execucao.ProvisionSandbox.chamadas == []

    async def test_grpc_unspecified_e_invalid_argument(self, stub_exec, execucao):
        """UNSPECIFIED no protobuf é a ausência, e a ausência não vira default."""
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.ProvisionSandbox(
                bff.ProvisionSandboxRequest(demand_id="dem-1"), metadata=CONTA
            )
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert execucao.ProvisionSandbox.chamadas == []

    def test_o_tier_declarado_desce_como_veio(self, cliente_exec, execucao):
        """Nem rebaixado "porque o cluster pode não ter", nem promovido."""
        cliente_exec.post(
            "/api/v1/sandboxes",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1", "min_tier": "hardware"},
        )
        pedido = execucao.ProvisionSandbox.pedidos[0]
        assert pedido.min_tier == execution_pb2.ISOLATION_TIER_HARDWARE
        assert pedido.idempotency_key != ""

    async def test_o_tier_declarado_desce_como_veio_no_grpc(self, stub_exec, execucao):
        await stub_exec.ProvisionSandbox(
            bff.ProvisionSandboxRequest(
                demand_id="dem-1",
                min_tier=bff.ISOLATION_TIER_KERNEL_EMULATED,
                idempotency_key="chave-do-cliente",
            ),
            metadata=CONTA,
        )
        pedido = execucao.ProvisionSandbox.pedidos[0]
        assert pedido.min_tier == execution_pb2.ISOLATION_TIER_KERNEL_EMULATED
        assert pedido.idempotency_key == "chave-do-cliente"

    def test_a_resposta_traz_o_tier_entregue(self, cliente_exec):
        """O cliente vê o que RECEBEU, não o que pediu (spec do substrato §2)."""
        s = cliente_exec.post(
            "/api/v1/sandboxes",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1", "min_tier": "namespace"},
        ).json()
        assert s["tier"] == "hardware"

    def test_recusa_do_nucleo_atravessa_como_412(self, cliente_exec, execucao):
        """Substrato sem o nível pedido = recusa com mensagem, não degradação."""
        execucao.ProvisionSandbox.falha_com(
            grpc.StatusCode.FAILED_PRECONDITION,
            "este substrato não oferece isolamento \"hardware\"",
        )
        r = cliente_exec.post(
            "/api/v1/sandboxes",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1", "min_tier": "hardware"},
        )
        assert r.status_code == 412
        assert "não oferece isolamento" in r.json()["detail"]


class TestDestruicao:
    def test_devolve_confirmacao_em_vez_de_204(self, cliente_exec):
        """Ato irreversível merece resposta que o cliente possa mostrar."""
        r = cliente_exec.delete("/api/v1/sandboxes/sbx-1", headers=CABECALHOS_REST)
        assert r.status_code == 200
        assert r.json() == {"destroyed": True}

    async def test_grpc_confirma_igual(self, stub_exec):
        resp = await stub_exec.DestroySandbox(
            bff.DestroySandboxRequest(id="sbx-1"), metadata=CONTA
        )
        assert resp.destroyed is True

    def test_suspender_e_destruir_sao_rpcs_diferentes(self, cliente_exec, execucao):
        """Suspender preserva o workspace; destruir o leva junto.

        O teste existe para travar a confusão mais cara possível neste domínio:
        um `DELETE` que na verdade suspendesse (ou um `/suspend` que destruísse)
        passaria em qualquer teste que só olhasse o código de status.
        """
        cliente_exec.post("/api/v1/sandboxes/sbx-1/suspend", headers=CABECALHOS_REST)
        assert len(execucao.SuspendSandbox.chamadas) == 1
        assert execucao.DestroySandbox.chamadas == []

        cliente_exec.delete("/api/v1/sandboxes/sbx-1", headers=CABECALHOS_REST)
        assert len(execucao.DestroySandbox.chamadas) == 1
        assert len(execucao.SuspendSandbox.chamadas) == 1

    def test_retomar_destruido_atravessa_com_o_motivo(self, cliente_exec, execucao):
        execucao.ResumeSandbox.falha_com(
            grpc.StatusCode.FAILED_PRECONDITION,
            "sandbox destruído não retoma — a destruição leva o workspace junto",
        )
        r = cliente_exec.post(
            "/api/v1/sandboxes/sbx-1/resume", headers=CABECALHOS_REST
        )
        assert r.status_code == 412
        assert "leva o workspace junto" in r.json()["detail"]


class TestPapel:
    def test_viewer_nao_provisiona(self, cliente_exec, nucleo, execucao):
        papel_viewer(nucleo)
        r = cliente_exec.post(
            "/api/v1/sandboxes",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1", "min_tier": "namespace"},
        )
        assert r.status_code == 403
        assert execucao.ProvisionSandbox.chamadas == []

    def test_viewer_nao_destroi(self, cliente_exec, nucleo, execucao):
        papel_viewer(nucleo)
        r = cliente_exec.delete("/api/v1/sandboxes/sbx-1", headers=CABECALHOS_REST)
        assert r.status_code == 403
        assert execucao.DestroySandbox.chamadas == []

    def test_viewer_enxerga_o_sandbox(self, cliente_exec, nucleo):
        """Ver em que isolamento a demanda roda é o que a spec quer visível."""
        papel_viewer(nucleo)
        r = cliente_exec.get("/api/v1/sandboxes/sbx-1", headers=CABECALHOS_REST)
        assert r.status_code == 200
        assert r.json()["tier"] == "hardware"

    def test_developer_altera_o_ciclo_de_vida(self, cliente_exec, nucleo):
        nucleo.papel_developer()
        r = cliente_exec.post(
            "/api/v1/sandboxes/sbx-1/suspend", headers=CABECALHOS_REST
        )
        assert r.status_code == 200

    async def test_viewer_nao_provisiona_no_grpc(self, stub_exec, nucleo):
        papel_viewer(nucleo)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.ProvisionSandbox(
                bff.ProvisionSandboxRequest(
                    demand_id="dem-1", min_tier=bff.ISOLATION_TIER_NAMESPACE
                ),
                metadata=CONTA,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED


class TestREST:
    def test_descrever_traz_endpoints(self, cliente_exec):
        s = cliente_exec.get(
            "/api/v1/sandboxes/sbx-1", headers=CABECALHOS_REST
        ).json()
        assert s["state"] == "active"
        assert s["namespace"] == "dop-dem1"
        assert s["endpoints"][0]["state"] == "running"

    def test_sem_atividade_vem_nulo_nao_zerado(self, cliente_exec, execucao):
        """Época zero faria a suspensão automática ler "ocioso desde 1970"."""
        execucao.DescribeSandbox.devolve(execucao.recem_criado)
        s = cliente_exec.get(
            "/api/v1/sandboxes/sbx-2", headers=CABECALHOS_REST
        ).json()
        assert s["last_active_at"] is None
        assert s["state"] == "provisioning"

    def test_sem_conta_ativa_e_recusado(self, cliente_exec):
        """Regra do SP-0, aplicada pelo decorator no CASO DE USO."""
        r = cliente_exec.get(
            "/api/v1/sandboxes/sbx-1", headers={"authorization": token_de()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_sem_token(self, stub_exec):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.DescribeSandbox(
                bff.DescribeSandboxRequest(id="sbx-1"), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_sem_atividade_fica_ausente(self, stub_exec, execucao):
        execucao.DescribeSandbox.devolve(execucao.recem_criado)
        resp = await stub_exec.DescribeSandbox(
            bff.DescribeSandboxRequest(id="sbx-2"), metadata=CONTA
        )
        assert not resp.HasField("last_active_at")

    async def test_detalhe_de_5xx_do_nucleo_nao_vaza(self, stub_exec, execucao):
        execucao.DescribeSandbox.falha_com(
            grpc.StatusCode.INTERNAL, "pq://user:senha@db:5432 caiu"
        )
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_exec.DescribeSandbox(
                bff.DescribeSandboxRequest(id="sbx-1"), metadata=CONTA
            )
        assert e.value.details() == "internal error"
        assert "senha" not in e.value.details()


class TestParidadeEntreTransportes:
    """O alarme que dispara se alguém reimplementar o caso de uso num adaptador."""

    async def test_descrever_igual_nas_duas_portas(self, cliente_exec, stub_exec):
        rest = cliente_exec.get(
            "/api/v1/sandboxes/sbx-1", headers=CABECALHOS_REST
        ).json()
        resp = await stub_exec.DescribeSandbox(
            bff.DescribeSandboxRequest(id="sbx-1"), metadata=CONTA
        )
        assert resp.id == rest["id"]
        assert resp.demand_id == rest["demand_id"]
        assert resp.namespace == rest["namespace"]
        # O enum da borda e o nome da borda dizem a MESMA coisa sobre o tier —
        # é aqui que uma tabela de conversão divergente apareceria.
        assert bff.IsolationTier.Name(resp.tier) == f"ISOLATION_TIER_{rest['tier'].upper()}"
        assert bff.Sandbox.State.Name(resp.state) == f"STATE_{rest['state'].upper()}"
        for g, j in zip(resp.endpoints, rest["endpoints"], strict=True):
            assert g.name == j["name"]
            assert g.url == j["url"]
            assert g.port == j["port"]
