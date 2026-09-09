import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AgentTurn(_message.Message):
    __slots__ = ("uuid", "parent_uuid", "occurred_at", "model", "service_tier", "stop_reason", "sidechain", "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens", "tool_uses", "thinking", "texts", "tools", "raw_usage")
    UUID_FIELD_NUMBER: _ClassVar[int]
    PARENT_UUID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    SERVICE_TIER_FIELD_NUMBER: _ClassVar[int]
    STOP_REASON_FIELD_NUMBER: _ClassVar[int]
    SIDECHAIN_FIELD_NUMBER: _ClassVar[int]
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_CREATION_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_READ_TOKENS_FIELD_NUMBER: _ClassVar[int]
    TOOL_USES_FIELD_NUMBER: _ClassVar[int]
    THINKING_FIELD_NUMBER: _ClassVar[int]
    TEXTS_FIELD_NUMBER: _ClassVar[int]
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    RAW_USAGE_FIELD_NUMBER: _ClassVar[int]
    uuid: str
    parent_uuid: str
    occurred_at: _timestamp_pb2.Timestamp
    model: str
    service_tier: str
    stop_reason: str
    sidechain: bool
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    tool_uses: int
    thinking: int
    texts: int
    tools: _containers.RepeatedScalarFieldContainer[str]
    raw_usage: str
    def __init__(self, uuid: _Optional[str] = ..., parent_uuid: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., model: _Optional[str] = ..., service_tier: _Optional[str] = ..., stop_reason: _Optional[str] = ..., sidechain: _Optional[bool] = ..., input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., cache_creation_tokens: _Optional[int] = ..., cache_read_tokens: _Optional[int] = ..., tool_uses: _Optional[int] = ..., thinking: _Optional[int] = ..., texts: _Optional[int] = ..., tools: _Optional[_Iterable[str]] = ..., raw_usage: _Optional[str] = ...) -> None: ...

class RecordTurnsRequest(_message.Message):
    __slots__ = ("session", "demand_id", "project_id", "cwd", "git_branch", "tool_version", "turns", "auth", "byte_offset")
    SESSION_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    CWD_FIELD_NUMBER: _ClassVar[int]
    GIT_BRANCH_FIELD_NUMBER: _ClassVar[int]
    TOOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    TURNS_FIELD_NUMBER: _ClassVar[int]
    AUTH_FIELD_NUMBER: _ClassVar[int]
    BYTE_OFFSET_FIELD_NUMBER: _ClassVar[int]
    session: str
    demand_id: str
    project_id: str
    cwd: str
    git_branch: str
    tool_version: str
    turns: _containers.RepeatedCompositeFieldContainer[AgentTurn]
    auth: SessionAuth
    byte_offset: int
    def __init__(self, session: _Optional[str] = ..., demand_id: _Optional[str] = ..., project_id: _Optional[str] = ..., cwd: _Optional[str] = ..., git_branch: _Optional[str] = ..., tool_version: _Optional[str] = ..., turns: _Optional[_Iterable[_Union[AgentTurn, _Mapping]]] = ..., auth: _Optional[_Union[SessionAuth, _Mapping]] = ..., byte_offset: _Optional[int] = ...) -> None: ...

class SessionAuth(_message.Message):
    __slots__ = ("method", "provider", "subscription", "key_source")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIPTION_FIELD_NUMBER: _ClassVar[int]
    KEY_SOURCE_FIELD_NUMBER: _ClassVar[int]
    method: str
    provider: str
    subscription: str
    key_source: str
    def __init__(self, method: _Optional[str] = ..., provider: _Optional[str] = ..., subscription: _Optional[str] = ..., key_source: _Optional[str] = ...) -> None: ...

class RecordTurnsResponse(_message.Message):
    __slots__ = ("recorded",)
    RECORDED_FIELD_NUMBER: _ClassVar[int]
    recorded: int
    def __init__(self, recorded: _Optional[int] = ...) -> None: ...

class GetDemandConsumptionRequest(_message.Message):
    __slots__ = ("demand_id",)
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    def __init__(self, demand_id: _Optional[str] = ...) -> None: ...

class DemandConsumption(_message.Message):
    __slots__ = ("demand_id", "sessions", "turns", "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens", "cache_ratio", "tokens_by_model", "calls_by_tool", "tokens_by_auth", "first_turn_at", "last_turn_at")
    class TokensByModelEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    class CallsByToolEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    class TokensByAuthEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    TURNS_FIELD_NUMBER: _ClassVar[int]
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_CREATION_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_READ_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_RATIO_FIELD_NUMBER: _ClassVar[int]
    TOKENS_BY_MODEL_FIELD_NUMBER: _ClassVar[int]
    CALLS_BY_TOOL_FIELD_NUMBER: _ClassVar[int]
    TOKENS_BY_AUTH_FIELD_NUMBER: _ClassVar[int]
    FIRST_TURN_AT_FIELD_NUMBER: _ClassVar[int]
    LAST_TURN_AT_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    sessions: int
    turns: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cache_ratio: float
    tokens_by_model: _containers.ScalarMap[str, int]
    calls_by_tool: _containers.ScalarMap[str, int]
    tokens_by_auth: _containers.ScalarMap[str, int]
    first_turn_at: _timestamp_pb2.Timestamp
    last_turn_at: _timestamp_pb2.Timestamp
    def __init__(self, demand_id: _Optional[str] = ..., sessions: _Optional[int] = ..., turns: _Optional[int] = ..., input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., cache_creation_tokens: _Optional[int] = ..., cache_read_tokens: _Optional[int] = ..., cache_ratio: _Optional[float] = ..., tokens_by_model: _Optional[_Mapping[str, int]] = ..., calls_by_tool: _Optional[_Mapping[str, int]] = ..., tokens_by_auth: _Optional[_Mapping[str, int]] = ..., first_turn_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_turn_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
