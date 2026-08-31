import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AccountRef(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class WorkspaceRef(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class ProjectRef(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class DemandRef(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class UserRef(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class ResourceRef(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class ActorRef(_message.Message):
    __slots__ = ("kind", "id", "name")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[ActorRef.Kind]
        KIND_USER: _ClassVar[ActorRef.Kind]
        KIND_AGENT: _ClassVar[ActorRef.Kind]
        KIND_SUBAGENT: _ClassVar[ActorRef.Kind]
        KIND_SYSTEM: _ClassVar[ActorRef.Kind]
    KIND_UNSPECIFIED: ActorRef.Kind
    KIND_USER: ActorRef.Kind
    KIND_AGENT: ActorRef.Kind
    KIND_SUBAGENT: ActorRef.Kind
    KIND_SYSTEM: ActorRef.Kind
    KIND_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    kind: ActorRef.Kind
    id: str
    name: str
    def __init__(self, kind: _Optional[_Union[ActorRef.Kind, str]] = ..., id: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class CallContext(_message.Message):
    __slots__ = ("account", "actor")
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    account: AccountRef
    actor: ActorRef
    def __init__(self, account: _Optional[_Union[AccountRef, _Mapping]] = ..., actor: _Optional[_Union[ActorRef, _Mapping]] = ...) -> None: ...

class PageRequest(_message.Message):
    __slots__ = ("size", "token")
    SIZE_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    size: int
    token: str
    def __init__(self, size: _Optional[int] = ..., token: _Optional[str] = ...) -> None: ...

class PageResponse(_message.Message):
    __slots__ = ("next_token", "total")
    NEXT_TOKEN_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    next_token: str
    total: int
    def __init__(self, next_token: _Optional[str] = ..., total: _Optional[int] = ...) -> None: ...

class Money(_message.Message):
    __slots__ = ("currency", "amount_micros")
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_MICROS_FIELD_NUMBER: _ClassVar[int]
    currency: str
    amount_micros: int
    def __init__(self, currency: _Optional[str] = ..., amount_micros: _Optional[int] = ...) -> None: ...

class AuditStamp(_message.Message):
    __slots__ = ("created_at", "updated_at", "created_by")
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    CREATED_BY_FIELD_NUMBER: _ClassVar[int]
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    created_by: ActorRef
    def __init__(self, created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., created_by: _Optional[_Union[ActorRef, _Mapping]] = ...) -> None: ...
