from app.grpcapi.gen.dop.bff.v1 import cost_pb2 as _cost_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RunTurnRequest(_message.Message):
    __slots__ = ("demand_id", "thread_id", "text", "task_kind", "provider", "operator_note", "max_output_tokens", "idempotency_key")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    TASK_KIND_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    OPERATOR_NOTE_FIELD_NUMBER: _ClassVar[int]
    MAX_OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    thread_id: str
    text: str
    task_kind: str
    provider: str
    operator_note: str
    max_output_tokens: int
    idempotency_key: str
    def __init__(self, demand_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., text: _Optional[str] = ..., task_kind: _Optional[str] = ..., provider: _Optional[str] = ..., operator_note: _Optional[str] = ..., max_output_tokens: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class RoutingView(_message.Message):
    __slots__ = ("task_kind", "model", "effort", "effort_applied", "reason", "from_agent_card")
    TASK_KIND_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    EFFORT_FIELD_NUMBER: _ClassVar[int]
    EFFORT_APPLIED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    FROM_AGENT_CARD_FIELD_NUMBER: _ClassVar[int]
    task_kind: str
    model: str
    effort: str
    effort_applied: str
    reason: str
    from_agent_card: bool
    def __init__(self, task_kind: _Optional[str] = ..., model: _Optional[str] = ..., effort: _Optional[str] = ..., effort_applied: _Optional[str] = ..., reason: _Optional[str] = ..., from_agent_card: _Optional[bool] = ...) -> None: ...

class TurnUsage(_message.Message):
    __slots__ = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost", "cache_creation_known", "cost_known")
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_READ_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_CREATION_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COST_FIELD_NUMBER: _ClassVar[int]
    CACHE_CREATION_KNOWN_FIELD_NUMBER: _ClassVar[int]
    COST_KNOWN_FIELD_NUMBER: _ClassVar[int]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost: _cost_pb2.Money
    cache_creation_known: bool
    cost_known: bool
    def __init__(self, input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., cache_read_tokens: _Optional[int] = ..., cache_creation_tokens: _Optional[int] = ..., cost: _Optional[_Union[_cost_pb2.Money, _Mapping]] = ..., cache_creation_known: _Optional[bool] = ..., cost_known: _Optional[bool] = ...) -> None: ...

class FindingRef(_message.Message):
    __slots__ = ("id", "title")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ...) -> None: ...

class TurnOutcome(_message.Message):
    __slots__ = ("demand_id", "thread_id", "provider", "routing", "reply", "message_ids", "concluded", "finding", "usage", "context_truncated", "paused", "notice", "budgets", "warnings")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    ROUTING_FIELD_NUMBER: _ClassVar[int]
    REPLY_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_IDS_FIELD_NUMBER: _ClassVar[int]
    CONCLUDED_FIELD_NUMBER: _ClassVar[int]
    FINDING_FIELD_NUMBER: _ClassVar[int]
    USAGE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    PAUSED_FIELD_NUMBER: _ClassVar[int]
    NOTICE_FIELD_NUMBER: _ClassVar[int]
    BUDGETS_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    thread_id: str
    provider: str
    routing: RoutingView
    reply: str
    message_ids: _containers.RepeatedScalarFieldContainer[str]
    concluded: bool
    finding: FindingRef
    usage: TurnUsage
    context_truncated: bool
    paused: bool
    notice: str
    budgets: _containers.RepeatedCompositeFieldContainer[_cost_pb2.BudgetView]
    warnings: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, demand_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., provider: _Optional[str] = ..., routing: _Optional[_Union[RoutingView, _Mapping]] = ..., reply: _Optional[str] = ..., message_ids: _Optional[_Iterable[str]] = ..., concluded: _Optional[bool] = ..., finding: _Optional[_Union[FindingRef, _Mapping]] = ..., usage: _Optional[_Union[TurnUsage, _Mapping]] = ..., context_truncated: _Optional[bool] = ..., paused: _Optional[bool] = ..., notice: _Optional[str] = ..., budgets: _Optional[_Iterable[_Union[_cost_pb2.BudgetView, _Mapping]]] = ..., warnings: _Optional[_Iterable[str]] = ...) -> None: ...
