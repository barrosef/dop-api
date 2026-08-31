import datetime

from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StreamEvent(_message.Message):
    __slots__ = ("id", "type", "aggregate", "aggregate_id", "payload", "occurred_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    AGGREGATE_FIELD_NUMBER: _ClassVar[int]
    AGGREGATE_ID_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    type: str
    aggregate: str
    aggregate_id: str
    payload: _struct_pb2.Struct
    occurred_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., type: _Optional[str] = ..., aggregate: _Optional[str] = ..., aggregate_id: _Optional[str] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class WatchEventsRequest(_message.Message):
    __slots__ = ("aggregate", "types", "since_event_id")
    AGGREGATE_FIELD_NUMBER: _ClassVar[int]
    TYPES_FIELD_NUMBER: _ClassVar[int]
    SINCE_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    aggregate: _containers.RepeatedScalarFieldContainer[str]
    types: _containers.RepeatedScalarFieldContainer[str]
    since_event_id: str
    def __init__(self, aggregate: _Optional[_Iterable[str]] = ..., types: _Optional[_Iterable[str]] = ..., since_event_id: _Optional[str] = ...) -> None: ...

class WatchDemandRequest(_message.Message):
    __slots__ = ("demand_id",)
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    def __init__(self, demand_id: _Optional[str] = ...) -> None: ...

class LogLine(_message.Message):
    __slots__ = ("source", "service", "line", "at")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    LINE_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    source: str
    service: str
    line: str
    at: _timestamp_pb2.Timestamp
    def __init__(self, source: _Optional[str] = ..., service: _Optional[str] = ..., line: _Optional[str] = ..., at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class TailLogsRequest(_message.Message):
    __slots__ = ("sandbox_id", "source", "service", "test_type")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    TEST_TYPE_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    source: str
    service: str
    test_type: str
    def __init__(self, sandbox_id: _Optional[str] = ..., source: _Optional[str] = ..., service: _Optional[str] = ..., test_type: _Optional[str] = ...) -> None: ...
