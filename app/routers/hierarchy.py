"""Rotas de hierarquia — tradução HTTP dos casos de uso, nada mais.

Zero decisão aqui: se você sentir vontade de escrever um `if` de regra nesta
camada, ele pertence a `app/usecases/hierarchy.py`, senão a porta gRPC fica
sem ele.
"""

from fastapi import APIRouter, Header

from app.usecases import hierarchy as uc
from app.usecases.hierarchy import (
    NewProject,
    NewWorkspace,
    ProjectSummary,
    TaskManagerBinding,
    TreeNode,
    WorkspaceSummary,
)

router = APIRouter(prefix="/api/v1", tags=["hierarquia"])

__all__ = [
    "NewProject",
    "NewWorkspace",
    "ProjectSummary",
    "TaskManagerBinding",
    "TreeNode",
    "WorkspaceSummary",
    "router",
]


@router.get("/tree", response_model=list[TreeNode])
async def get_tree() -> list[TreeNode]:
    """Árvore da conta ativa — o que a barra lateral do cockpit desenha."""
    return await uc.get_tree()


@router.get("/workspaces", response_model=list[WorkspaceSummary])
async def list_workspaces() -> list[WorkspaceSummary]:
    return await uc.list_workspaces()


@router.post("/workspaces", response_model=WorkspaceSummary, status_code=201)
async def create_workspace(
    body: NewWorkspace,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> WorkspaceSummary:
    return await uc.create_workspace(body, idempotency_key)


@router.get("/projects", response_model=list[ProjectSummary])
async def list_projects(workspace_id: str = "") -> list[ProjectSummary]:
    return await uc.list_projects(workspace_id)


@router.post("/projects", response_model=ProjectSummary, status_code=201)
async def create_project(
    body: NewProject,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> ProjectSummary:
    return await uc.create_project(body, idempotency_key)


@router.get("/projects/{project_id}", response_model=ProjectSummary)
async def get_project(project_id: str) -> ProjectSummary:
    return await uc.get_project(project_id)


@router.put("/projects/{project_id}/task-manager", response_model=ProjectSummary)
async def bind_task_manager(project_id: str, body: TaskManagerBinding) -> ProjectSummary:
    return await uc.bind_task_manager(project_id, body)
