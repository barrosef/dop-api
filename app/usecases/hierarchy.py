"""Hierarchy use cases — workspaces and projects.

The same discipline as `identity`: the rule lives here and only here; the router
and the servicer translate. See `app/usecases/identity.py`'s docstring for why
the decorators live in the use case and not in the adapter.

Vocabulary, because confusing the two is expensive: **a workspace is OUR
concept**, the hierarchy's level 1. The task provider's "space" (Jira, ClickUp)
is another thing, and appears as `external_space_id` in the project's binding.
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


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


# ── the edge's models ───────────────────────────────────────────────────────


class WorkspaceSummary(BaseModel):
    id: str
    name: str
    key: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class NewWorkspace(BaseModel):
    name: str = Field(min_length=1)
    # The key is short and uppercase: it is what prefixes a demand's identifier,
    # and the core refuses anything else.
    key: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class TaskManagerBinding(BaseModel):
    """The project's binding to the provider's board.

    `external_space_id` is THEIR space, never our word workspace. `card_types`
    comes from the provider and is dynamic — the platform imposes no card
    vocabulary.
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


# ── translating the core into the edge ──────────────────────────────────────


def _workspace(w: hierarchy_pb2.Workspace) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=w.id, name=w.name, key=w.key, description=w.description, tags=list(w.tags)
    )


def _project(p: hierarchy_pb2.Project) -> ProjectSummary:
    binding = None
    # HasField only works for a message: an absent task_manager and a zeroed one
    # are different things, and treating them as the same would invent a
    # binding.
    if p.HasField("task_manager"):
        tm = p.task_manager
        binding = TaskManagerBinding(
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
        task_manager=binding,
    )


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
async def get_tree() -> list[TreeNode]:
    """The active account's whole tree, in a single response.

    The cockpit draws its navigation from it; fetching the workspaces and then a
    ListProjects per workspace would make the sidebar flicker across N requests.
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
    """Creating a workspace reorganizes the whole account — hence requiring owner or admin."""
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
    """The account's projects; with `workspace_id`, only that workspace's."""
    ctx = auth_ctx.get()
    request = hierarchy_pb2.ListProjectsRequest(ctx=call_context_from(ctx))
    if workspace_id:
        request.workspace.id = workspace_id
    resp = await stubs.hierarchy_stub().ListProjects(
        request, metadata=core.metadata(), timeout=_deadline()
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
    """The account is NOT supplied: it is inherited from the workspace.

    Letting the caller supply it would open the way for a project to be born in
    an account that is not its own workspace's.
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
    """Binds the project to the provider's board.

    UpdateProject in the core is a REPLACEMENT: sending the project without the
    fields you do not want to lose erases them. That is why we read the current
    project first and resend what was already there — the edge is not going to
    make the cockpit guess that trap.
    """
    ctx = auth_ctx.get()
    stub = stubs.hierarchy_stub()
    current = await stub.GetProject(
        hierarchy_pb2.GetProjectRequest(ctx=call_context_from(ctx), id=project_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    current.task_manager.CopyFrom(
        hierarchy_pb2.ProjectTaskManager(
            integration=common_pb2.ResourceRef(id=body.integration_id),
            external_space_id=body.external_space_id,
            external_project_id=body.external_project_id,
            card_types=body.card_types,
        )
    )
    p = await stub.UpdateProject(
        hierarchy_pb2.UpdateProjectRequest(ctx=call_context_from(ctx), project=current),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _project(p)
