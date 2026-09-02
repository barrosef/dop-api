"""The hierarchy gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/hierarchy.py`: it receives a message, calls the SAME
function from `app/usecases/hierarchy.py`, returns a message. No decisions here
— neither authorization nor a call to the core.
"""

from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2_grpc as bff_grpc
from app.usecases import hierarchy as uc


def _workspace(w: uc.WorkspaceSummary) -> bff.Workspace:
    return bff.Workspace(
        id=w.id, name=w.name, key=w.key, description=w.description, tags=w.tags
    )


def _project(p: uc.ProjectSummary) -> bff.Project:
    msg = bff.Project(
        id=p.id,
        workspace_id=p.workspace_id,
        name=p.name,
        description=p.description,
        rules=p.rules,
    )
    # It only fills in when it exists: an absent message field and a zeroed one
    # are different things, and assigning an empty one would invent a binding
    # where there is none.
    if p.task_manager is not None:
        msg.task_manager.CopyFrom(
            bff.TaskManagerBinding(
                integration_id=p.task_manager.integration_id,
                external_space_id=p.task_manager.external_space_id,
                external_project_id=p.task_manager.external_project_id,
                card_types=p.task_manager.card_types,
            )
        )
    return msg


class HierarchyServicer(bff_grpc.HierarchyServiceServicer):
    async def GetTree(self, request: bff.GetTreeRequest, context) -> bff.GetTreeResponse:
        return bff.GetTreeResponse(
            nodes=[
                bff.TreeNode(
                    workspace=_workspace(n.workspace),
                    projects=[_project(p) for p in n.projects],
                )
                for n in await uc.get_tree()
            ]
        )

    async def ListWorkspaces(
        self, request: bff.ListWorkspacesRequest, context
    ) -> bff.ListWorkspacesResponse:
        return bff.ListWorkspacesResponse(
            workspaces=[_workspace(w) for w in await uc.list_workspaces()]
        )

    async def CreateWorkspace(
        self, request: bff.CreateWorkspaceRequest, context
    ) -> bff.Workspace:
        body = uc.NewWorkspace(
            name=request.name,
            key=request.key,
            description=request.description,
            tags=list(request.tags),
        )
        return _workspace(await uc.create_workspace(body, request.idempotency_key))

    async def ListProjects(
        self, request: bff.ListProjectsRequest, context
    ) -> bff.ListProjectsResponse:
        return bff.ListProjectsResponse(
            projects=[_project(p) for p in await uc.list_projects(request.workspace_id)]
        )

    async def GetProject(self, request: bff.GetProjectRequest, context) -> bff.Project:
        return _project(await uc.get_project(request.id))

    async def CreateProject(self, request: bff.CreateProjectRequest, context) -> bff.Project:
        body = uc.NewProject(
            workspace_id=request.workspace_id,
            name=request.name,
            description=request.description,
        )
        return _project(await uc.create_project(body, request.idempotency_key))

    async def BindTaskManager(
        self, request: bff.BindTaskManagerRequest, context
    ) -> bff.Project:
        b = request.binding
        vinculo = uc.TaskManagerBinding(
            integration_id=b.integration_id,
            external_space_id=b.external_space_id,
            external_project_id=b.external_project_id,
            card_types=list(b.card_types),
        )
        return _project(await uc.bind_task_manager(request.project_id, vinculo))
