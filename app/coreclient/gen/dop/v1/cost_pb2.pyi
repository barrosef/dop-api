import datetime

from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class UsageEvent(_message.Message):
    __slots__ = ("id", "demand", "thread_id", "model", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost", "at")
    ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_READ_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_CREATION_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COST_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    demand: _common_pb2.DemandRef
    thread_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost: _common_pb2.Money
    at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., demand: _Optional[_Union[_common_pb2.DemandRef, _Mapping]] = ..., thread_id: _Optional[str] = ..., model: _Optional[str] = ..., input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., cache_read_tokens: _Optional[int] = ..., cache_creation_tokens: _Optional[int] = ..., cost: _Optional[_Union[_common_pb2.Money, _Mapping]] = ..., at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Budget(_message.Message):
    __slots__ = ("scope", "scope_id", "limit_micros", "spent_micros", "currency")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_MICROS_FIELD_NUMBER: _ClassVar[int]
    SPENT_MICROS_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    scope: str
    scope_id: str
    limit_micros: int
    spent_micros: int
    currency: str
    def __init__(self, scope: _Optional[str] = ..., scope_id: _Optional[str] = ..., limit_micros: _Optional[int] = ..., spent_micros: _Optional[int] = ..., currency: _Optional[str] = ...) -> None: ...

class RoutingDecision(_message.Message):
    __slots__ = ("task_kind", "model", "effort", "reason")
    TASK_KIND_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    EFFORT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    task_kind: str
    model: str
    effort: str
    reason: str
    def __init__(self, task_kind: _Optional[str] = ..., model: _Optional[str] = ..., effort: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class RecordUsageRequest(_message.Message):
    __slots__ = ("usage", "idempotency_key")
    USAGE_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    usage: UsageEvent
    idempotency_key: str
    def __init__(self, usage: _Optional[_Union[UsageEvent, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class RecordUsageResponse(_message.Message):
    __slots__ = ("recorded", "budget_exceeded")
    RECORDED_FIELD_NUMBER: _ClassVar[int]
    BUDGET_EXCEEDED_FIELD_NUMBER: _ClassVar[int]
    recorded: bool
    budget_exceeded: bool
    def __init__(self, recorded: _Optional[bool] = ..., budget_exceeded: _Optional[bool] = ...) -> None: ...

class GetBudgetRequest(_message.Message):
    __slots__ = ("scope", "scope_id")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    scope: str
    scope_id: str
    def __init__(self, scope: _Optional[str] = ..., scope_id: _Optional[str] = ...) -> None: ...

class SetBudgetRequest(_message.Message):
    __slots__ = ("budget",)
    BUDGET_FIELD_NUMBER: _ClassVar[int]
    budget: Budget
    def __init__(self, budget: _Optional[_Union[Budget, _Mapping]] = ...) -> None: ...

class RouteModelRequest(_message.Message):
    __slots__ = ("task_kind", "demand_id")
    TASK_KIND_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    task_kind: str
    demand_id: str
    def __init__(self, task_kind: _Optional[str] = ..., demand_id: _Optional[str] = ...) -> None: ...

class SummarizeCostRequest(_message.Message):
    __slots__ = ("scope", "scope_id")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    scope: str
    scope_id: str
    def __init__(self, scope: _Optional[str] = ..., scope_id: _Optional[str] = ...) -> None: ...

class SummarizeCostResponse(_message.Message):
    __slots__ = ("total", "cache_hit_ratio", "recent")
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    CACHE_HIT_RATIO_FIELD_NUMBER: _ClassVar[int]
    RECENT_FIELD_NUMBER: _ClassVar[int]
    total: _common_pb2.Money
    cache_hit_ratio: float
    recent: _containers.RepeatedCompositeFieldContainer[UsageEvent]
    def __init__(self, total: _Optional[_Union[_common_pb2.Money, _Mapping]] = ..., cache_hit_ratio: _Optional[float] = ..., recent: _Optional[_Iterable[_Union[UsageEvent, _Mapping]]] = ...) -> None: ...
