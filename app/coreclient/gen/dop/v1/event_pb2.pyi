import datetime

from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EventEnvelope(_message.Message):
    __slots__ = ("id", "account", "aggregate", "aggregate_id", "type", "payload", "occurred_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    AGGREGATE_FIELD_NUMBER: _ClassVar[int]
    AGGREGATE_ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    account: _common_pb2.AccountRef
    aggregate: str
    aggregate_id: str
    type: str
    payload: _struct_pb2.Struct
    occurred_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., account: _Optional[_Union[_common_pb2.AccountRef, _Mapping]] = ..., aggregate: _Optional[str] = ..., aggregate_id: _Optional[str] = ..., type: _Optional[str] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class WatchEventsRequest(_message.Message):
    __slots__ = ("ctx", "aggregate", "types", "since_event_id")
    CTX_FIELD_NUMBER: _ClassVar[int]
    AGGREGATE_FIELD_NUMBER: _ClassVar[int]
    TYPES_FIELD_NUMBER: _ClassVar[int]
    SINCE_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    aggregate: _containers.RepeatedScalarFieldContainer[str]
    types: _containers.RepeatedScalarFieldContainer[str]
    since_event_id: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., aggregate: _Optional[_Iterable[str]] = ..., types: _Optional[_Iterable[str]] = ..., since_event_id: _Optional[str] = ...) -> None: ...
