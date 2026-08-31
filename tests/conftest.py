"""Duplos do núcleo. Nenhum teste sobe o dop-core de verdade.

O ponto de substituição é UM só: `app.coreclient.stubs.identity_stub`. Router,
servicer gRPC e resolver passam por ele, então trocar essa função troca o
núcleo inteiro — sem patch espalhado por módulo.

As fixtures da porta gRPC (`servidor_grpc`, `stub_grpc`) sobem um servidor de
verdade em porta efêmera, contra o mesmo núcleo falso das fixtures REST. É o
que permite pedir a mesma coisa pelas duas portas e comparar o resultado.
"""

import base64
import json
import time

import grpc
import pytest
from fastapi.testclient import TestClient
from grpc.aio import AioRpcError

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, hierarchy_pb2, identity_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2_grpc as bff_hier_grpc
from app.grpcapi.gen.dop.bff.v1 import identity_pb2_grpc as bff_grpc
from app.grpcapi.server import GrpcServer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.settings import settings

PROJECT = "dop-local"


def token_de(subject="sub-1", email="dev@dop.local", name="Dev"):
    """Token do emulador: não é assinado, mas normaliza igual ao de produção."""
    payload = {
        "sub": subject,
        "aud": PROJECT,
        "exp": time.time() + 3600,
        "email": email,
        "email_verified": True,
        "name": name,
        "firebase": {"sign_in_provider": "password", "identities": {"email": [email]}},
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"Bearer cabecalho.{raw}.assinatura"


def erro_grpc(code: grpc.StatusCode, details="veio do núcleo") -> AioRpcError:
    return AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details)


class ChamadaFalsa:
    """Um RPC. Guarda o que recebeu; devolve o que mandaram devolver."""

    def __init__(self, resultado=None):
        self.resultado = resultado
        self.chamadas: list[dict] = []

    def devolve(self, resultado):
        self.resultado = resultado
        return self

    def falha_com(self, code: grpc.StatusCode, details="veio do núcleo"):
        self.resultado = erro_grpc(code, details)
        return self

    @property
    def ultima(self) -> dict:
        assert self.chamadas, "o RPC não foi chamado"
        return self.chamadas[-1]

    def metadados(self) -> dict[str, str]:
        return dict(self.ultima["metadata"])

    @property
    def pedidos(self):
        """Só as mensagens, sem o resto — o que a maioria dos testes quer."""
        return [c["request"] for c in self.chamadas]

    async def __call__(self, request, *, metadata=None, timeout=None, **_):
        self.chamadas.append({"request": request, "metadata": metadata, "timeout": timeout})
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


class NucleoFalso:
    """Stub do IdentityService com os RPCs que a borda usa."""

    def __init__(self, user_id="u-1", account_id="acct-1", role=identity_pb2.ROLE_ADMIN):
        self.user_id = user_id
        conta = identity_pb2.Account(
            id=account_id,
            kind=identity_pb2.Account.KIND_ORGANIZATION,
            handle="acme",
            display_name="ACME",
        )
        self.EnsureUser = ChamadaFalsa(identity_pb2.User(id=user_id, email="dev@dop.local"))
        self.ListAccounts = ChamadaFalsa(
            identity_pb2.ListAccountsResponse(accounts=[conta])
        )
        self.ListMemberships = ChamadaFalsa(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(
                        id="m-1",
                        user=common_pb2.UserRef(id=user_id),
                        account=common_pb2.AccountRef(id=account_id),
                        role=role,
                    )
                ]
            )
        )
        self.conta = conta
        self.CreateAccount = ChamadaFalsa(conta)
        self.CreateInvite = ChamadaFalsa(
            identity_pb2.Invite(
                id="inv-1",
                email="novo@dop.local",
                role=identity_pb2.ROLE_DEVELOPER,
                status=identity_pb2.Invite.STATUS_PENDING,
            )
        )


    def papel_developer(self):
        """Rebaixa o ator a developer.

        O papel é resolvido UMA vez, no login, a partir de ListMemberships —
        então mudar o papel é trocar o que esse RPC responde, não um atalho
        no contexto. Testar pelo atalho provaria menos do que parece.
        """
        self.ListMemberships = ChamadaFalsa(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(
                        id="m-1",
                        user=common_pb2.UserRef(id=self.user_id),
                        account=common_pb2.AccountRef(id=self.conta.id),
                        role=identity_pb2.ROLE_DEVELOPER,
                    )
                ]
            )
        )


@pytest.fixture
def nucleo(monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    falso = NucleoFalso()
    monkeypatch.setattr(stubs, "identity_stub", lambda: falso)
    return falso


@pytest.fixture
def cliente(nucleo):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _porta_grpc_desligada(monkeypatch):
    """Nenhum teste abre a porta gRPC pelo lifespan.

    Cada `create_app()` num teste tentaria ouvir na MESMA porta fixa da
    configuração — a segunda falharia, e a suíte ficaria dependente da ordem.
    Quem testa a porta gRPC a cria explicitamente, em porta efêmera.
    """
    monkeypatch.setattr(settings, "grpc_enabled", False)


def metadados_de(token: str | None = None, account_id: str = "acct-1", **extra):
    """Metadados gRPC equivalentes aos cabeçalhos do REST.

    Identidade no `authorization` (não no corpo da mensagem) e conta ativa no
    `x-account-id`, exatamente como o AuthMiddleware espera no HTTP.
    """
    md = []
    if token is not None:
        md.append(("authorization", token))
    if account_id:
        md.append(("x-account-id", account_id))
    md += list(extra.items())
    return md


@pytest.fixture
async def servidor_grpc(nucleo):
    """Servidor gRPC real, em porta efêmera, contra o núcleo falso.

    Real de propósito: interceptor que só é exercido por chamada direta não
    prova que o `grpc.aio` o executa na ordem certa nem que o ContextVar
    sobrevive até o servicer — que é justamente a parte difícil.
    """
    servidor = GrpcServer(
        verifier=FirebaseVerifier(PROJECT),
        resolver=CoreResolver(),
        port=0,
        host="127.0.0.1",
    )
    await servidor.start()
    try:
        yield servidor
    finally:
        await servidor.stop(grace=0)


@pytest.fixture
async def stub_grpc(servidor_grpc):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor_grpc.port}") as canal:
        yield bff_grpc.IdentityServiceStub(canal)


# ── hierarquia ──────────────────────────────────────────────────────────────


class HierarquiaFalsa:
    """Núcleo falso de hierarquia. Mesma disciplina do NucleoFalso: registra o
    pedido e os metadados que chegaram, para que o teste de paridade possa
    provar que REST e gRPC montam a MESMA requisição."""

    def __init__(self):
        ws = hierarchy_pb2.Workspace(
            id="ws-1",
            account=common_pb2.AccountRef(id="acct-1"),
            name="Plataforma",
            key="PLAT",
            description="workspace de teste",
            tags=["ativo"],
        )
        prj = hierarchy_pb2.Project(
            id="prj-1",
            workspace=common_pb2.WorkspaceRef(id="ws-1"),
            name="Cockpit",
            description="projeto de teste",
            rules=["no-green-no-pr"],
        )
        prj.task_manager.CopyFrom(
            hierarchy_pb2.ProjectTaskManager(
                integration=common_pb2.ResourceRef(id="res-1"),
                external_space_id="sp-901",
                external_project_id="pj-42",
                card_types=["bug", "feature"],
            )
        )
        # Projeto SEM quadro: é o caso que distingue ausente de zerado.
        sem_quadro = hierarchy_pb2.Project(
            id="prj-2",
            workspace=common_pb2.WorkspaceRef(id="ws-1"),
            name="Sem quadro",
        )
        self.workspace, self.projeto, self.projeto_sem_quadro = ws, prj, sem_quadro

        self.GetTree = ChamadaFalsa(
            hierarchy_pb2.GetTreeResponse(
                nodes=[
                    hierarchy_pb2.GetTreeResponse.Node(
                        workspace=ws, projects=[prj, sem_quadro]
                    )
                ]
            )
        )
        self.ListWorkspaces = ChamadaFalsa(
            hierarchy_pb2.ListWorkspacesResponse(workspaces=[ws])
        )
        self.CreateWorkspace = ChamadaFalsa(ws)
        self.ListProjects = ChamadaFalsa(
            hierarchy_pb2.ListProjectsResponse(projects=[prj, sem_quadro])
        )
        self.GetProject = ChamadaFalsa(prj)
        self.CreateProject = ChamadaFalsa(prj)
        self.UpdateProject = ChamadaFalsa(prj)


@pytest.fixture
def hierarquia(nucleo, monkeypatch):
    falso = HierarquiaFalsa()
    monkeypatch.setattr(stubs, "hierarchy_stub", lambda: falso)
    return falso


@pytest.fixture
def cliente_hier(hierarquia):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
async def stub_hier(servidor_grpc, hierarquia):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor_grpc.port}") as canal:
        yield bff_hier_grpc.HierarchyServiceStub(canal)
