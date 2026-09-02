import datetime

from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AttentionItem(_message.Message):
    __slots__ = ("id", "account", "kind", "target_kind", "target_id", "demand", "title", "summary", "priority", "opened_at", "resolved_at")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[AttentionItem.Kind]
        KIND_THREAD_BLOCKED: _ClassVar[AttentionItem.Kind]
        KIND_GATE_PENDING: _ClassVar[AttentionItem.Kind]
        KIND_PR_REVIEW: _ClassVar[AttentionItem.Kind]
        KIND_MERGE_CONFLICT: _ClassVar[AttentionItem.Kind]
        KIND_DIRECTIVE: _ClassVar[AttentionItem.Kind]
        KIND_BUDGET_EXCEEDED: _ClassVar[AttentionItem.Kind]
        KIND_INTEGRATION_BROKEN: _ClassVar[AttentionItem.Kind]
    KIND_UNSPECIFIED: AttentionItem.Kind
    KIND_THREAD_BLOCKED: AttentionItem.Kind
    KIND_GATE_PENDING: AttentionItem.Kind
    KIND_PR_REVIEW: AttentionItem.Kind
    KIND_MERGE_CONFLICT: AttentionItem.Kind
    KIND_DIRECTIVE: AttentionItem.Kind
    KIND_BUDGET_EXCEEDED: AttentionItem.Kind
    KIND_INTEGRATION_BROKEN: AttentionItem.Kind
    ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TARGET_KIND_FIELD_NUMBER: _ClassVar[int]
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    OPENED_AT_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    account: _common_pb2.AccountRef
    kind: AttentionItem.Kind
    target_kind: str
    target_id: str
    demand: _common_pb2.DemandRef
    title: str
    summary: str
    priority: int
    opened_at: _timestamp_pb2.Timestamp
    resolved_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., account: _Optional[_Union[_common_pb2.AccountRef, _Mapping]] = ..., kind: _Optional[_Union[AttentionItem.Kind, str]] = ..., target_kind: _Optional[str] = ..., target_id: _Optional[str] = ..., demand: _Optional[_Union[_common_pb2.DemandRef, _Mapping]] = ..., title: _Optional[str] = ..., summary: _Optional[str] = ..., priority: _Optional[int] = ..., opened_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., resolved_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListAttentionRequest(_message.Message):
    __slots__ = ("include_resolved", "demand", "page")
    INCLUDE_RESOLVED_FIELD_NUMBER: _ClassVar[int]
    DEMAND_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    include_resolved: bool
    demand: _common_pb2.DemandRef
    page: _common_pb2.PageRequest
    def __init__(self, include_resolved: _Optional[bool] = ..., demand: _Optional[_Union[_common_pb2.DemandRef, _Mapping]] = ..., page: _Optional[_Union[_common_pb2.PageRequest, _Mapping]] = ...) -> None: ...

class ListAttentionResponse(_message.Message):
    __slots__ = ("items", "page", "open_total")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    OPEN_TOTAL_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[AttentionItem]
    page: _common_pb2.PageResponse
    open_total: int
    def __init__(self, items: _Optional[_Iterable[_Union[AttentionItem, _Mapping]]] = ..., page: _Optional[_Union[_common_pb2.PageResponse, _Mapping]] = ..., open_total: _Optional[int] = ...) -> None: ...

class WatchAttentionRequest(_message.Message):
    __slots__ = ("since_event_id",)
    SINCE_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    since_event_id: str
    def __init__(self, since_event_id: _Optional[str] = ...) -> None: ...

class AttentionUpdate(_message.Message):
    __slots__ = ("change", "item", "event_id")
    class Change(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        CHANGE_UNSPECIFIED: _ClassVar[AttentionUpdate.Change]
        CHANGE_OPENED: _ClassVar[AttentionUpdate.Change]
        CHANGE_RESOLVED: _ClassVar[AttentionUpdate.Change]
    CHANGE_UNSPECIFIED: AttentionUpdate.Change
    CHANGE_OPENED: AttentionUpdate.Change
    CHANGE_RESOLVED: AttentionUpdate.Change
    CHANGE_FIELD_NUMBER: _ClassVar[int]
    ITEM_FIELD_NUMBER: _ClassVar[int]
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    change: AttentionUpdate.Change
    item: AttentionItem
    event_id: str
    def __init__(self, change: _Optional[_Union[AttentionUpdate.Change, str]] = ..., item: _Optional[_Union[AttentionItem, _Mapping]] = ..., event_id: _Optional[str] = ...) -> None: ...
