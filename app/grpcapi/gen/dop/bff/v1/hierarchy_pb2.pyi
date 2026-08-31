from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Workspace(_message.Message):
    __slots__ = ("id", "name", "key", "description", "tags")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    key: str
    description: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., key: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ...) -> None: ...

class TaskManagerBinding(_message.Message):
    __slots__ = ("integration_id", "external_space_id", "external_project_id", "card_types")
    INTEGRATION_ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_SPACE_ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    CARD_TYPES_FIELD_NUMBER: _ClassVar[int]
    integration_id: str
    external_space_id: str
    external_project_id: str
    card_types: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, integration_id: _Optional[str] = ..., external_space_id: _Optional[str] = ..., external_project_id: _Optional[str] = ..., card_types: _Optional[_Iterable[str]] = ...) -> None: ...

class Project(_message.Message):
    __slots__ = ("id", "workspace_id", "name", "description", "rules", "task_manager")
    ID_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    TASK_MANAGER_FIELD_NUMBER: _ClassVar[int]
    id: str
    workspace_id: str
    name: str
    description: str
    rules: _containers.RepeatedScalarFieldContainer[str]
    task_manager: TaskManagerBinding
    def __init__(self, id: _Optional[str] = ..., workspace_id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., rules: _Optional[_Iterable[str]] = ..., task_manager: _Optional[_Union[TaskManagerBinding, _Mapping]] = ...) -> None: ...

class TreeNode(_message.Message):
    __slots__ = ("workspace", "projects")
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    PROJECTS_FIELD_NUMBER: _ClassVar[int]
    workspace: Workspace
    projects: _containers.RepeatedCompositeFieldContainer[Project]
    def __init__(self, workspace: _Optional[_Union[Workspace, _Mapping]] = ..., projects: _Optional[_Iterable[_Union[Project, _Mapping]]] = ...) -> None: ...

class GetTreeRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetTreeResponse(_message.Message):
    __slots__ = ("nodes",)
    NODES_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[TreeNode]
    def __init__(self, nodes: _Optional[_Iterable[_Union[TreeNode, _Mapping]]] = ...) -> None: ...

class ListWorkspacesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListWorkspacesResponse(_message.Message):
    __slots__ = ("workspaces",)
    WORKSPACES_FIELD_NUMBER: _ClassVar[int]
    workspaces: _containers.RepeatedCompositeFieldContainer[Workspace]
    def __init__(self, workspaces: _Optional[_Iterable[_Union[Workspace, _Mapping]]] = ...) -> None: ...

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

class ListProjectsRequest(_message.Message):
    __slots__ = ("workspace_id",)
    WORKSPACE_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_id: str
    def __init__(self, workspace_id: _Optional[str] = ...) -> None: ...

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
    __slots__ = ("workspace_id", "name", "description", "idempotency_key")
    WORKSPACE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    workspace_id: str
    name: str
    description: str
    idempotency_key: str
    def __init__(self, workspace_id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class BindTaskManagerRequest(_message.Message):
    __slots__ = ("project_id", "binding")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    BINDING_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    binding: TaskManagerBinding
    def __init__(self, project_id: _Optional[str] = ..., binding: _Optional[_Union[TaskManagerBinding, _Mapping]] = ...) -> None: ...
