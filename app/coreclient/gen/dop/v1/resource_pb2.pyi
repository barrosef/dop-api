from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Resource(_message.Message):
    __slots__ = ("id", "account", "kind", "name", "version", "config", "credential_ref", "audit")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[Resource.Kind]
        KIND_INTEGRATION: _ClassVar[Resource.Kind]
        KIND_SKILL: _ClassVar[Resource.Kind]
        KIND_WORKFLOW: _ClassVar[Resource.Kind]
        KIND_GIT_FLOW: _ClassVar[Resource.Kind]
    KIND_UNSPECIFIED: Resource.Kind
    KIND_INTEGRATION: Resource.Kind
    KIND_SKILL: Resource.Kind
    KIND_WORKFLOW: Resource.Kind
    KIND_GIT_FLOW: Resource.Kind
    ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_REF_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    account: _common_pb2.AccountRef
    kind: Resource.Kind
    name: str
    version: int
    config: _struct_pb2.Struct
    credential_ref: str
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., account: _Optional[_Union[_common_pb2.AccountRef, _Mapping]] = ..., kind: _Optional[_Union[Resource.Kind, str]] = ..., name: _Optional[str] = ..., version: _Optional[int] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., credential_ref: _Optional[str] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class IntegrationSpec(_message.Message):
    __slots__ = ("category", "provider", "base_url", "auth_method", "status", "connected_by")
    class Category(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        CATEGORY_UNSPECIFIED: _ClassVar[IntegrationSpec.Category]
        CATEGORY_GIT: _ClassVar[IntegrationSpec.Category]
        CATEGORY_TASK_MANAGER: _ClassVar[IntegrationSpec.Category]
        CATEGORY_AGENT: _ClassVar[IntegrationSpec.Category]
    CATEGORY_UNSPECIFIED: IntegrationSpec.Category
    CATEGORY_GIT: IntegrationSpec.Category
    CATEGORY_TASK_MANAGER: IntegrationSpec.Category
    CATEGORY_AGENT: IntegrationSpec.Category
    class AuthMethod(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        AUTH_METHOD_UNSPECIFIED: _ClassVar[IntegrationSpec.AuthMethod]
        AUTH_METHOD_OAUTH_APP: _ClassVar[IntegrationSpec.AuthMethod]
        AUTH_METHOD_OAUTH_USER: _ClassVar[IntegrationSpec.AuthMethod]
        AUTH_METHOD_TOKEN: _ClassVar[IntegrationSpec.AuthMethod]
        AUTH_METHOD_SSH_KEY: _ClassVar[IntegrationSpec.AuthMethod]
    AUTH_METHOD_UNSPECIFIED: IntegrationSpec.AuthMethod
    AUTH_METHOD_OAUTH_APP: IntegrationSpec.AuthMethod
    AUTH_METHOD_OAUTH_USER: IntegrationSpec.AuthMethod
    AUTH_METHOD_TOKEN: IntegrationSpec.AuthMethod
    AUTH_METHOD_SSH_KEY: IntegrationSpec.AuthMethod
    class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATUS_UNSPECIFIED: _ClassVar[IntegrationSpec.Status]
        STATUS_ACTIVE: _ClassVar[IntegrationSpec.Status]
        STATUS_EXPIRED: _ClassVar[IntegrationSpec.Status]
        STATUS_REVOKED: _ClassVar[IntegrationSpec.Status]
        STATUS_ERROR: _ClassVar[IntegrationSpec.Status]
    STATUS_UNSPECIFIED: IntegrationSpec.Status
    STATUS_ACTIVE: IntegrationSpec.Status
    STATUS_EXPIRED: IntegrationSpec.Status
    STATUS_REVOKED: IntegrationSpec.Status
    STATUS_ERROR: IntegrationSpec.Status
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    BASE_URL_FIELD_NUMBER: _ClassVar[int]
    AUTH_METHOD_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_BY_FIELD_NUMBER: _ClassVar[int]
    category: IntegrationSpec.Category
    provider: str
    base_url: str
    auth_method: IntegrationSpec.AuthMethod
    status: IntegrationSpec.Status
    connected_by: _common_pb2.UserRef
    def __init__(self, category: _Optional[_Union[IntegrationSpec.Category, str]] = ..., provider: _Optional[str] = ..., base_url: _Optional[str] = ..., auth_method: _Optional[_Union[IntegrationSpec.AuthMethod, str]] = ..., status: _Optional[_Union[IntegrationSpec.Status, str]] = ..., connected_by: _Optional[_Union[_common_pb2.UserRef, _Mapping]] = ...) -> None: ...

class ResourceGrant(_message.Message):
    __slots__ = ("id", "resource", "user", "level")
    ID_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    resource: _common_pb2.ResourceRef
    user: _common_pb2.UserRef
    level: str
    def __init__(self, id: _Optional[str] = ..., resource: _Optional[_Union[_common_pb2.ResourceRef, _Mapping]] = ..., user: _Optional[_Union[_common_pb2.UserRef, _Mapping]] = ..., level: _Optional[str] = ...) -> None: ...

class ListResourcesRequest(_message.Message):
    __slots__ = ("kind", "page")
    KIND_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    kind: Resource.Kind
    page: _common_pb2.PageRequest
    def __init__(self, kind: _Optional[_Union[Resource.Kind, str]] = ..., page: _Optional[_Union[_common_pb2.PageRequest, _Mapping]] = ...) -> None: ...

class ListResourcesResponse(_message.Message):
    __slots__ = ("resources", "page")
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    resources: _containers.RepeatedCompositeFieldContainer[Resource]
    page: _common_pb2.PageResponse
    def __init__(self, resources: _Optional[_Iterable[_Union[Resource, _Mapping]]] = ..., page: _Optional[_Union[_common_pb2.PageResponse, _Mapping]] = ...) -> None: ...

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
    kind: Resource.Kind
    name: str
    config: _struct_pb2.Struct
    idempotency_key: str
    def __init__(self, kind: _Optional[_Union[Resource.Kind, str]] = ..., name: _Optional[str] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class UpdateResourceRequest(_message.Message):
    __slots__ = ("id", "config")
    ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    id: str
    config: _struct_pb2.Struct
    def __init__(self, id: _Optional[str] = ..., config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class DeleteResourceRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class DeleteResourceResponse(_message.Message):
    __slots__ = ("deleted",)
    DELETED_FIELD_NUMBER: _ClassVar[int]
    deleted: bool
    def __init__(self, deleted: _Optional[bool] = ...) -> None: ...

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

class SetCredentialRequest(_message.Message):
    __slots__ = ("resource_id", "secret")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    SECRET_FIELD_NUMBER: _ClassVar[int]
    resource_id: str
    secret: bytes
    def __init__(self, resource_id: _Optional[str] = ..., secret: _Optional[bytes] = ...) -> None: ...

class SetCredentialResponse(_message.Message):
    __slots__ = ("credential_ref",)
    CREDENTIAL_REF_FIELD_NUMBER: _ClassVar[int]
    credential_ref: str
    def __init__(self, credential_ref: _Optional[str] = ...) -> None: ...
