import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AttentionKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ATTENTION_KIND_UNSPECIFIED: _ClassVar[AttentionKind]
    ATTENTION_KIND_THREAD_BLOCKED: _ClassVar[AttentionKind]
    ATTENTION_KIND_GATE_PENDING: _ClassVar[AttentionKind]
    ATTENTION_KIND_PR_REVIEW: _ClassVar[AttentionKind]
    ATTENTION_KIND_MERGE_CONFLICT: _ClassVar[AttentionKind]
    ATTENTION_KIND_DIRECTIVE: _ClassVar[AttentionKind]
    ATTENTION_KIND_BUDGET_EXCEEDED: _ClassVar[AttentionKind]
    ATTENTION_KIND_INTEGRATION_BROKEN: _ClassVar[AttentionKind]

class AttentionChange(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ATTENTION_CHANGE_UNSPECIFIED: _ClassVar[AttentionChange]
    ATTENTION_CHANGE_OPENED: _ClassVar[AttentionChange]
    ATTENTION_CHANGE_RESOLVED: _ClassVar[AttentionChange]
ATTENTION_KIND_UNSPECIFIED: AttentionKind
ATTENTION_KIND_THREAD_BLOCKED: AttentionKind
ATTENTION_KIND_GATE_PENDING: AttentionKind
ATTENTION_KIND_PR_REVIEW: AttentionKind
ATTENTION_KIND_MERGE_CONFLICT: AttentionKind
ATTENTION_KIND_DIRECTIVE: AttentionKind
ATTENTION_KIND_BUDGET_EXCEEDED: AttentionKind
ATTENTION_KIND_INTEGRATION_BROKEN: AttentionKind
ATTENTION_CHANGE_UNSPECIFIED: AttentionChange
ATTENTION_CHANGE_OPENED: AttentionChange
ATTENTION_CHANGE_RESOLVED: AttentionChange

class AttentionItem(_message.Message):
    __slots__ = ("id", "kind", "target_kind", "target_id", "demand_id", "title", "summary", "priority", "opened_at", "resolved_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TARGET_KIND_FIELD_NUMBER: _ClassVar[int]
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    OPENED_AT_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: AttentionKind
    target_kind: str
    target_id: str
    demand_id: str
    title: str
    summary: str
    priority: int
    opened_at: _timestamp_pb2.Timestamp
    resolved_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[_Union[AttentionKind, str]] = ..., target_kind: _Optional[str] = ..., target_id: _Optional[str] = ..., demand_id: _Optional[str] = ..., title: _Optional[str] = ..., summary: _Optional[str] = ..., priority: _Optional[int] = ..., opened_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., resolved_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AttentionGroup(_message.Message):
    __slots__ = ("demand_id", "items")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    items: _containers.RepeatedCompositeFieldContainer[AttentionItem]
    def __init__(self, demand_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[AttentionItem, _Mapping]]] = ...) -> None: ...

class AttentionBox(_message.Message):
    __slots__ = ("items", "groups", "open_total")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    GROUPS_FIELD_NUMBER: _ClassVar[int]
    OPEN_TOTAL_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[AttentionItem]
    groups: _containers.RepeatedCompositeFieldContainer[AttentionGroup]
    open_total: int
    def __init__(self, items: _Optional[_Iterable[_Union[AttentionItem, _Mapping]]] = ..., groups: _Optional[_Iterable[_Union[AttentionGroup, _Mapping]]] = ..., open_total: _Optional[int] = ...) -> None: ...

class ListAttentionRequest(_message.Message):
    __slots__ = ("include_resolved", "demand_id", "page_size")
    INCLUDE_RESOLVED_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    include_resolved: bool
    demand_id: str
    page_size: int
    def __init__(self, include_resolved: _Optional[bool] = ..., demand_id: _Optional[str] = ..., page_size: _Optional[int] = ...) -> None: ...

class AttentionUpdate(_message.Message):
    __slots__ = ("change", "item")
    CHANGE_FIELD_NUMBER: _ClassVar[int]
    ITEM_FIELD_NUMBER: _ClassVar[int]
    change: AttentionChange
    item: AttentionItem
    def __init__(self, change: _Optional[_Union[AttentionChange, str]] = ..., item: _Optional[_Union[AttentionItem, _Mapping]] = ...) -> None: ...

class WatchAttentionRequest(_message.Message):
    __slots__ = ("since_event_id",)
    SINCE_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    since_event_id: str
    def __init__(self, since_event_id: _Optional[str] = ...) -> None: ...
