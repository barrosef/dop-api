"""Hierarchy routes — the use cases' HTTP translation, nothing more.

Zero decisions here: if you feel the urge to write a rule's `if` in this layer,
it belongs in `app/usecases/hierarchy.py`, or the gRPC port ends up without it.
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

router = APIRouter(prefix="/api/v1", tags=["hierarchy"])

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
    """The active account's tree — what the cockpit's sidebar draws."""
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
