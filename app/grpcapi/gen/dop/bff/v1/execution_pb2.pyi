import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class IsolationTier(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ISOLATION_TIER_UNSPECIFIED: _ClassVar[IsolationTier]
    ISOLATION_TIER_HARDWARE: _ClassVar[IsolationTier]
    ISOLATION_TIER_KERNEL_EMULATED: _ClassVar[IsolationTier]
    ISOLATION_TIER_NAMESPACE: _ClassVar[IsolationTier]
ISOLATION_TIER_UNSPECIFIED: IsolationTier
ISOLATION_TIER_HARDWARE: IsolationTier
ISOLATION_TIER_KERNEL_EMULATED: IsolationTier
ISOLATION_TIER_NAMESPACE: IsolationTier

class SandboxEndpoint(_message.Message):
    __slots__ = ("name", "url", "port", "state")
    NAME_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    name: str
    url: str
    port: int
    state: str
    def __init__(self, name: _Optional[str] = ..., url: _Optional[str] = ..., port: _Optional[int] = ..., state: _Optional[str] = ...) -> None: ...

class Sandbox(_message.Message):
    __slots__ = ("id", "demand_id", "state", "tier", "namespace", "endpoints", "last_active_at")
    class State(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATE_UNSPECIFIED: _ClassVar[Sandbox.State]
        STATE_PROVISIONING: _ClassVar[Sandbox.State]
        STATE_ACTIVE: _ClassVar[Sandbox.State]
        STATE_SUSPENDED: _ClassVar[Sandbox.State]
        STATE_DESTROYED: _ClassVar[Sandbox.State]
    STATE_UNSPECIFIED: Sandbox.State
    STATE_PROVISIONING: Sandbox.State
    STATE_ACTIVE: Sandbox.State
    STATE_SUSPENDED: Sandbox.State
    STATE_DESTROYED: Sandbox.State
    ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    TIER_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    ENDPOINTS_FIELD_NUMBER: _ClassVar[int]
    LAST_ACTIVE_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    demand_id: str
    state: Sandbox.State
    tier: IsolationTier
    namespace: str
    endpoints: _containers.RepeatedCompositeFieldContainer[SandboxEndpoint]
    last_active_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., demand_id: _Optional[str] = ..., state: _Optional[_Union[Sandbox.State, str]] = ..., tier: _Optional[_Union[IsolationTier, str]] = ..., namespace: _Optional[str] = ..., endpoints: _Optional[_Iterable[_Union[SandboxEndpoint, _Mapping]]] = ..., last_active_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ProvisionSandboxRequest(_message.Message):
    __slots__ = ("demand_id", "min_tier", "idempotency_key")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    MIN_TIER_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    min_tier: IsolationTier
    idempotency_key: str
    def __init__(self, demand_id: _Optional[str] = ..., min_tier: _Optional[_Union[IsolationTier, str]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class DescribeSandboxRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class SuspendSandboxRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class ResumeSandboxRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class DestroySandboxRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class DestroySandboxResponse(_message.Message):
    __slots__ = ("destroyed",)
    DESTROYED_FIELD_NUMBER: _ClassVar[int]
    destroyed: bool
    def __init__(self, destroyed: _Optional[bool] = ...) -> None: ...
