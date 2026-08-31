from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ResourceKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RESOURCE_KIND_UNSPECIFIED: _ClassVar[ResourceKind]
    RESOURCE_KIND_INTEGRATION: _ClassVar[ResourceKind]
    RESOURCE_KIND_SKILL: _ClassVar[ResourceKind]
    RESOURCE_KIND_WORKFLOW: _ClassVar[ResourceKind]
    RESOURCE_KIND_GIT_FLOW: _ClassVar[ResourceKind]
RESOURCE_KIND_UNSPECIFIED: ResourceKind
RESOURCE_KIND_INTEGRATION: ResourceKind
RESOURCE_KIND_SKILL: ResourceKind
RESOURCE_KIND_WORKFLOW: ResourceKind
RESOURCE_KIND_GIT_FLOW: ResourceKind

class Resource(_message.Message):
    __slots__ = ("id", "kind", "name", "version", "config", "credential_ref")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_REF_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: ResourceKind
    name: str
    version: int
    config: _struct_pb2.Struct
    credential_ref: str
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[_Union[ResourceKind, str]] = ..., name: _Optional[str] = ..., version: _Optional[int] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., credential_ref: _Optional[str] = ...) -> None: ...

class Grant(_message.Message):
    __slots__ = ("id", "resource_id", "user_id", "level")
    ID_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    resource_id: str
    user_id: str
    level: str
    def __init__(self, id: _Optional[str] = ..., resource_id: _Optional[str] = ..., user_id: _Optional[str] = ..., level: _Optional[str] = ...) -> None: ...

class ListResourcesRequest(_message.Message):
    __slots__ = ("kind",)
    KIND_FIELD_NUMBER: _ClassVar[int]
    kind: ResourceKind
    def __init__(self, kind: _Optional[_Union[ResourceKind, str]] = ...) -> None: ...

class ListResourcesResponse(_message.Message):
    __slots__ = ("resources",)
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    resources: _containers.RepeatedCompositeFieldContainer[Resource]
    def __init__(self, resources: _Optional[_Iterable[_Union[Resource, _Mapping]]] = ...) -> None: ...

class GetResourceRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class CreateResourceRequest(_message.Message):
    __slots__ = ("kind", "name", "config", "idempotency_key")
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    kind: ResourceKind
    name: str
    config: _struct_pb2.Struct
    idempotency_key: str
    def __init__(self, kind: _Optional[_Union[ResourceKind, str]] = ..., name: _Optional[str] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class SetCredentialRequest(_message.Message):
    __slots__ = ("resource_id", "secret")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    SECRET_FIELD_NUMBER: _ClassVar[int]
    resource_id: str
    secret: bytes
    def __init__(self, resource_id: _Optional[str] = ..., secret: _Optional[bytes] = ...) -> None: ...

class GrantResourceRequest(_message.Message):
    __slots__ = ("resource_id", "user_id", "level")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    resource_id: str
    user_id: str
    level: str
    def __init__(self, resource_id: _Optional[str] = ..., user_id: _Optional[str] = ..., level: _Optional[str] = ...) -> None: ...

class RevokeGrantRequest(_message.Message):
    __slots__ = ("grant_id",)
    GRANT_ID_FIELD_NUMBER: _ClassVar[int]
    grant_id: str
    def __init__(self, grant_id: _Optional[str] = ...) -> None: ...

class RevokeGrantResponse(_message.Message):
    __slots__ = ("revoked",)
    REVOKED_FIELD_NUMBER: _ClassVar[int]
    revoked: bool
    def __init__(self, revoked: _Optional[bool] = ...) -> None: ...
