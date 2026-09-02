from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Workspace(_message.Message):
    __slots__ = ("id", "account", "name", "key", "description", "tags", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    account: _common_pb2.AccountRef
    name: str
    key: str
    description: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., account: _Optional[_Union[_common_pb2.AccountRef, _Mapping]] = ..., name: _Optional[str] = ..., key: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class Project(_message.Message):
    __slots__ = ("id", "workspace", "name", "description", "repos", "task_manager", "resources", "rules", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    REPOS_FIELD_NUMBER: _ClassVar[int]
    TASK_MANAGER_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    workspace: _common_pb2.WorkspaceRef
    name: str
    description: str
    repos: _containers.RepeatedCompositeFieldContainer[ProjectRepo]
    task_manager: ProjectTaskManager
    resources: _containers.RepeatedCompositeFieldContainer[_common_pb2.ResourceRef]
    rules: _containers.RepeatedScalarFieldContainer[str]
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., workspace: _Optional[_Union[_common_pb2.WorkspaceRef, _Mapping]] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., repos: _Optional[_Iterable[_Union[ProjectRepo, _Mapping]]] = ..., task_manager: _Optional[_Union[ProjectTaskManager, _Mapping]] = ..., resources: _Optional[_Iterable[_Union[_common_pb2.ResourceRef, _Mapping]]] = ..., rules: _Optional[_Iterable[str]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class ProjectRepo(_message.Message):
    __slots__ = ("id", "integration", "external_id", "name", "default_branch", "pr_targets")
    ID_FIELD_NUMBER: _ClassVar[int]
    INTEGRATION_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_BRANCH_FIELD_NUMBER: _ClassVar[int]
    PR_TARGETS_FIELD_NUMBER: _ClassVar[int]
    id: str
    integration: _common_pb2.ResourceRef
    external_id: str
    name: str
    default_branch: str
    pr_targets: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., integration: _Optional[_Union[_common_pb2.ResourceRef, _Mapping]] = ..., external_id: _Optional[str] = ..., name: _Optional[str] = ..., default_branch: _Optional[str] = ..., pr_targets: _Optional[_Iterable[str]] = ...) -> None: ...

class ProjectTaskManager(_message.Message):
    __slots__ = ("integration", "external_space_id", "external_project_id", "card_types")
    INTEGRATION_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_SPACE_ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    CARD_TYPES_FIELD_NUMBER: _ClassVar[int]
    integration: _common_pb2.ResourceRef
    external_space_id: str
    external_project_id: str
    card_types: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, integration: _Optional[_Union[_common_pb2.ResourceRef, _Mapping]] = ..., external_space_id: _Optional[str] = ..., external_project_id: _Optional[str] = ..., card_types: _Optional[_Iterable[str]] = ...) -> None: ...

class ListWorkspacesRequest(_message.Message):
    __slots__ = ("page",)
    PAGE_FIELD_NUMBER: _ClassVar[int]
    page: _common_pb2.PageRequest
    def __init__(self, page: _Optional[_Union[_common_pb2.PageRequest, _Mapping]] = ...) -> None: ...

class ListWorkspacesResponse(_message.Message):
    __slots__ = ("workspaces", "page")
    WORKSPACES_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    workspaces: _containers.RepeatedCompositeFieldContainer[Workspace]
    page: _common_pb2.PageResponse
    def __init__(self, workspaces: _Optional[_Iterable[_Union[Workspace, _Mapping]]] = ..., page: _Optional[_Union[_common_pb2.PageResponse, _Mapping]] = ...) -> None: ...

class GetWorkspaceRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class CreateWorkspaceRequest(_message.Message):
    __slots__ = ("name", "key", "description", "tags", "idempotency_key")
    NAME_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    name: str
    key: str
    description: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    idempotency_key: str
    def __init__(self, name: _Optional[str] = ..., key: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class UpdateWorkspaceRequest(_message.Message):
    __slots__ = ("workspace",)
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    workspace: Workspace
    def __init__(self, workspace: _Optional[_Union[Workspace, _Mapping]] = ...) -> None: ...

class ListProjectsRequest(_message.Message):
    __slots__ = ("workspace",)
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    workspace: _common_pb2.WorkspaceRef
    def __init__(self, workspace: _Optional[_Union[_common_pb2.WorkspaceRef, _Mapping]] = ...) -> None: ...

class ListProjectsResponse(_message.Message):
    __slots__ = ("projects",)
    PROJECTS_FIELD_NUMBER: _ClassVar[int]
    projects: _containers.RepeatedCompositeFieldContainer[Project]
    def __init__(self, projects: _Optional[_Iterable[_Union[Project, _Mapping]]] = ...) -> None: ...

class GetProjectRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class CreateProjectRequest(_message.Message):
    __slots__ = ("workspace", "name", "description", "idempotency_key")
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    workspace: _common_pb2.WorkspaceRef
    name: str
    description: str
    idempotency_key: str
    def __init__(self, workspace: _Optional[_Union[_common_pb2.WorkspaceRef, _Mapping]] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class UpdateProjectRequest(_message.Message):
    __slots__ = ("project",)
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    project: Project
    def __init__(self, project: _Optional[_Union[Project, _Mapping]] = ...) -> None: ...

class GetTreeRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetTreeResponse(_message.Message):
    __slots__ = ("nodes",)
    class Node(_message.Message):
        __slots__ = ("workspace", "projects")
        WORKSPACE_FIELD_NUMBER: _ClassVar[int]
        PROJECTS_FIELD_NUMBER: _ClassVar[int]
        workspace: Workspace
        projects: _containers.RepeatedCompositeFieldContainer[Project]
        def __init__(self, workspace: _Optional[_Union[Workspace, _Mapping]] = ..., projects: _Optional[_Iterable[_Union[Project, _Mapping]]] = ...) -> None: ...
    NODES_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[GetTreeResponse.Node]
    def __init__(self, nodes: _Optional[_Iterable[_Union[GetTreeResponse.Node, _Mapping]]] = ...) -> None: ...
