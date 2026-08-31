"""Casos de uso de hierarquia — workspaces e projetos.

Mesma disciplina de `identity`: a regra vive aqui e só aqui; router e servicer
traduzem. Ver o docstring de `app/usecases/identity.py` para o porquê dos
decorators morarem no caso de uso e não no adaptador.

Vocabulário, porque confundir custa caro: **workspace é conceito NOSSO**, o
nível 1 da hierarquia. O "espaço" do provedor de tarefas (Jira, ClickUp) é
outra coisa, e aparece como `external_space_id` no vínculo do projeto.
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import common_pb2, hierarchy_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


# ── modelos da borda ────────────────────────────────────────────────────────


class WorkspaceSummary(BaseModel):
    id: str
    name: str
    key: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class NewWorkspace(BaseModel):
    name: str = Field(min_length=1)
    # A chave é curta e maiúscula: é ela que prefixa identificador de demanda,
    # e o núcleo recusa qualquer outra coisa.
    key: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class TaskManagerBinding(BaseModel):
    """Vínculo do projeto com o quadro do provedor.

    `external_space_id` é o espaço DELE, nunca a nossa palavra workspace.
    `card_types` vem do provedor e é dinâmico — a plataforma não impõe
    vocabulário de card.
    """

    integration_id: str
    external_space_id: str = ""
    external_project_id: str = ""
    card_types: list[str] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str = ""
    rules: list[str] = Field(default_factory=list)
    task_manager: TaskManagerBinding | None = None


class NewProject(BaseModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""


class TreeNode(BaseModel):
    workspace: WorkspaceSummary
    projects: list[ProjectSummary] = Field(default_factory=list)


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _workspace(w: hierarchy_pb2.Workspace) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=w.id, name=w.name, key=w.key, description=w.description, tags=list(w.tags)
    )


def _project(p: hierarchy_pb2.Project) -> ProjectSummary:
    vinculo = None
    # HasField só vale para mensagem: um task_manager ausente e um zerado são
    # coisas diferentes, e tratar os dois como iguais inventaria vínculo.
    if p.HasField("task_manager"):
        tm = p.task_manager
        vinculo = TaskManagerBinding(
            integration_id=tm.integration.id,
            external_space_id=tm.external_space_id,
            external_project_id=tm.external_project_id,
            card_types=list(tm.card_types),
        )
    return ProjectSummary(
        id=p.id,
        workspace_id=p.workspace.id,
        name=p.name,
        description=p.description,
        rules=list(p.rules),
        task_manager=vinculo,
    )


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
async def get_tree() -> list[TreeNode]:
    """A árvore inteira da conta ativa, numa resposta só.

    O cockpit desenha a navegação a partir dela; buscar workspace e depois um
    ListProjects por workspace faria a barra lateral piscar em N requisições.
    """
    ctx = auth_ctx.get()
    resp = await stubs.hierarchy_stub().GetTree(
        hierarchy_pb2.GetTreeRequest(ctx=call_context_from(ctx)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [
        TreeNode(
            workspace=_workspace(n.workspace),
            projects=[_project(p) for p in n.projects],
        )
        for n in resp.nodes
    ]


@log
@account_scoped
async def list_workspaces() -> list[WorkspaceSummary]:
    ctx = auth_ctx.get()
    resp = await stubs.hierarchy_stub().ListWorkspaces(
        hierarchy_pb2.ListWorkspacesRequest(ctx=call_context_from(ctx)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_workspace(w) for w in resp.workspaces]


@log
@account_scoped
@require_role("owner", "admin")
async def create_workspace(body: NewWorkspace, idempotency_key: str = "") -> WorkspaceSummary:
    """Criar workspace reorganiza a conta inteira — daí exigir owner ou admin."""
    ctx = auth_ctx.get()
    w = await stubs.hierarchy_stub().CreateWorkspace(
        hierarchy_pb2.CreateWorkspaceRequest(
            ctx=call_context_from(ctx),
            name=body.name,
            key=body.key,
            description=body.description,
            tags=body.tags,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _workspace(w)


@log
@account_scoped
async def list_projects(workspace_id: str = "") -> list[ProjectSummary]:
    """Projetos da conta; com `workspace_id`, só os daquele workspace."""
    ctx = auth_ctx.get()
    pedido = hierarchy_pb2.ListProjectsRequest(ctx=call_context_from(ctx))
    if workspace_id:
        pedido.workspace.id = workspace_id
    resp = await stubs.hierarchy_stub().ListProjects(
        pedido, metadata=core.metadata(), timeout=_deadline()
    )
    return [_project(p) for p in resp.projects]


@log
@account_scoped
async def get_project(project_id: str) -> ProjectSummary:
    ctx = auth_ctx.get()
    p = await stubs.hierarchy_stub().GetProject(
        hierarchy_pb2.GetProjectRequest(ctx=call_context_from(ctx), id=project_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _project(p)


@log
@account_scoped
@require_role("owner", "admin")
async def create_project(body: NewProject, idempotency_key: str = "") -> ProjectSummary:
    """A conta NÃO é informada: ela é herdada do workspace.

    Deixar o chamador informá-la abriria caminho para projeto nascer numa conta
    que não é a do seu próprio workspace.
    """
    ctx = auth_ctx.get()
    p = await stubs.hierarchy_stub().CreateProject(
        hierarchy_pb2.CreateProjectRequest(
            ctx=call_context_from(ctx),
            workspace=common_pb2.WorkspaceRef(id=body.workspace_id),
            name=body.name,
            description=body.description,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _project(p)


@log
@account_scoped
@require_role("owner", "admin")
async def bind_task_manager(project_id: str, body: TaskManagerBinding) -> ProjectSummary:
    """Vincula o projeto ao quadro do provedor.

    UpdateProject no núcleo é SUBSTITUIÇÃO: mandar o projeto sem os campos que
    não se quer perder os apaga. Por isso lemos o projeto atual antes e
    reenviamos o que já estava lá — a borda não vai fazer o cockpit adivinhar
    essa armadilha.
    """
    ctx = auth_ctx.get()
    stub = stubs.hierarchy_stub()
    atual = await stub.GetProject(
        hierarchy_pb2.GetProjectRequest(ctx=call_context_from(ctx), id=project_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    atual.task_manager.CopyFrom(
        hierarchy_pb2.ProjectTaskManager(
            integration=common_pb2.ResourceRef(id=body.integration_id),
            external_space_id=body.external_space_id,
            external_project_id=body.external_project_id,
            card_types=body.card_types,
        )
    )
    p = await stub.UpdateProject(
        hierarchy_pb2.UpdateProjectRequest(ctx=call_context_from(ctx), project=atual),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _project(p)
