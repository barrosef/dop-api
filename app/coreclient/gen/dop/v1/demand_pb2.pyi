import datetime

from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from app.coreclient.gen.dop.v1 import workflow_pb2 as _workflow_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class DopStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DOP_STATUS_UNSPECIFIED: _ClassVar[DopStatus]
    DOP_STATUS_NEW: _ClassVar[DopStatus]
    DOP_STATUS_DOING: _ClassVar[DopStatus]
    DOP_STATUS_DONE: _ClassVar[DopStatus]
    DOP_STATUS_DELIVERED: _ClassVar[DopStatus]

class StageStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STAGE_STATUS_UNSPECIFIED: _ClassVar[StageStatus]
    STAGE_STATUS_PENDING: _ClassVar[StageStatus]
    STAGE_STATUS_RUNNING: _ClassVar[StageStatus]
    STAGE_STATUS_BLOCKED: _ClassVar[StageStatus]
    STAGE_STATUS_DONE: _ClassVar[StageStatus]
DOP_STATUS_UNSPECIFIED: DopStatus
DOP_STATUS_NEW: DopStatus
DOP_STATUS_DOING: DopStatus
DOP_STATUS_DONE: DopStatus
DOP_STATUS_DELIVERED: DopStatus
STAGE_STATUS_UNSPECIFIED: StageStatus
STAGE_STATUS_PENDING: StageStatus
STAGE_STATUS_RUNNING: StageStatus
STAGE_STATUS_BLOCKED: StageStatus
STAGE_STATUS_DONE: StageStatus

class Demand(_message.Message):
    __slots__ = ("id", "project", "external_key", "title", "card_type", "provider_status", "dop_status", "flow_id", "flow_version", "stages", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_KEY_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CARD_TYPE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_STATUS_FIELD_NUMBER: _ClassVar[int]
    DOP_STATUS_FIELD_NUMBER: _ClassVar[int]
    FLOW_ID_FIELD_NUMBER: _ClassVar[int]
    FLOW_VERSION_FIELD_NUMBER: _ClassVar[int]
    STAGES_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    project: _common_pb2.ProjectRef
    external_key: str
    title: str
    card_type: str
    provider_status: str
    dop_status: DopStatus
    flow_id: str
    flow_version: int
    stages: _containers.RepeatedCompositeFieldContainer[DemandStage]
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., project: _Optional[_Union[_common_pb2.ProjectRef, _Mapping]] = ..., external_key: _Optional[str] = ..., title: _Optional[str] = ..., card_type: _Optional[str] = ..., provider_status: _Optional[str] = ..., dop_status: _Optional[_Union[DopStatus, str]] = ..., flow_id: _Optional[str] = ..., flow_version: _Optional[int] = ..., stages: _Optional[_Iterable[_Union[DemandStage, _Mapping]]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class DemandStage(_message.Message):
    __slots__ = ("key", "name", "type", "status", "gate", "artifacts", "started_at", "finished_at")
    KEY_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    GATE_FIELD_NUMBER: _ClassVar[int]
    ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    key: str
    name: str
    type: _workflow_pb2.StageType
    status: StageStatus
    gate: _workflow_pb2.Gate
    artifacts: _containers.RepeatedCompositeFieldContainer[Artifact]
    started_at: _timestamp_pb2.Timestamp
    finished_at: _timestamp_pb2.Timestamp
    def __init__(self, key: _Optional[str] = ..., name: _Optional[str] = ..., type: _Optional[_Union[_workflow_pb2.StageType, str]] = ..., status: _Optional[_Union[StageStatus, str]] = ..., gate: _Optional[_Union[_workflow_pb2.Gate, str]] = ..., artifacts: _Optional[_Iterable[_Union[Artifact, _Mapping]]] = ..., started_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., finished_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Artifact(_message.Message):
    __slots__ = ("id", "kind", "name", "object_ref", "version", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    OBJECT_REF_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: _workflow_pb2.ArtifactKind
    name: str
    object_ref: str
    version: int
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[_Union[_workflow_pb2.ArtifactKind, str]] = ..., name: _Optional[str] = ..., object_ref: _Optional[str] = ..., version: _Optional[int] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class Thread(_message.Message):
    __slots__ = ("id", "demand", "key", "card", "blocked", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    CARD_FIELD_NUMBER: _ClassVar[int]
    BLOCKED_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    demand: _common_pb2.DemandRef
    key: str
    card: AgentCard
    blocked: bool
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., demand: _Optional[_Union[_common_pb2.DemandRef, _Mapping]] = ..., key: _Optional[str] = ..., card: _Optional[_Union[AgentCard, _Mapping]] = ..., blocked: _Optional[bool] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class AgentCard(_message.Message):
    __slots__ = ("purpose", "tools", "model", "effort", "budget_micros")
    PURPOSE_FIELD_NUMBER: _ClassVar[int]
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    EFFORT_FIELD_NUMBER: _ClassVar[int]
    BUDGET_MICROS_FIELD_NUMBER: _ClassVar[int]
    purpose: str
    tools: _containers.RepeatedScalarFieldContainer[str]
    model: str
    effort: str
    budget_micros: int
    def __init__(self, purpose: _Optional[str] = ..., tools: _Optional[_Iterable[str]] = ..., model: _Optional[str] = ..., effort: _Optional[str] = ..., budget_micros: _Optional[int] = ...) -> None: ...

class Message(_message.Message):
    __slots__ = ("id", "thread_id", "author", "text", "at")
    ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    thread_id: str
    author: _common_pb2.ActorRef
    text: str
    at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., thread_id: _Optional[str] = ..., author: _Optional[_Union[_common_pb2.ActorRef, _Mapping]] = ..., text: _Optional[str] = ..., at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Finding(_message.Message):
    __slots__ = ("id", "demand", "thread_id", "title", "payload", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    demand: _common_pb2.DemandRef
    thread_id: str
    title: str
    payload: _struct_pb2.Struct
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., demand: _Optional[_Union[_common_pb2.DemandRef, _Mapping]] = ..., thread_id: _Optional[str] = ..., title: _Optional[str] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class ListDemandsRequest(_message.Message):
    __slots__ = ("project", "page")
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    project: _common_pb2.ProjectRef
    page: _common_pb2.PageRequest
    def __init__(self, project: _Optional[_Union[_common_pb2.ProjectRef, _Mapping]] = ..., page: _Optional[_Union[_common_pb2.PageRequest, _Mapping]] = ...) -> None: ...

class ListDemandsResponse(_message.Message):
    __slots__ = ("demands", "page")
    DEMANDS_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    demands: _containers.RepeatedCompositeFieldContainer[Demand]
    page: _common_pb2.PageResponse
    def __init__(self, demands: _Optional[_Iterable[_Union[Demand, _Mapping]]] = ..., page: _Optional[_Union[_common_pb2.PageResponse, _Mapping]] = ...) -> None: ...

class GetDemandRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class StartDemandRequest(_message.Message):
    __slots__ = ("project", "external_key", "idempotency_key")
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_KEY_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    project: _common_pb2.ProjectRef
    external_key: str
    idempotency_key: str
    def __init__(self, project: _Optional[_Union[_common_pb2.ProjectRef, _Mapping]] = ..., external_key: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class AdvanceStageRequest(_message.Message):
    __slots__ = ("demand_id", "stage_key", "status", "idempotency_key")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    STAGE_KEY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    stage_key: str
    status: StageStatus
    idempotency_key: str
    def __init__(self, demand_id: _Optional[str] = ..., stage_key: _Optional[str] = ..., status: _Optional[_Union[StageStatus, str]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class DecideGateRequest(_message.Message):
    __slots__ = ("demand_id", "stage_key", "approved", "comment", "idempotency_key")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    STAGE_KEY_FIELD_NUMBER: _ClassVar[int]
    APPROVED_FIELD_NUMBER: _ClassVar[int]
    COMMENT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    stage_key: str
    approved: bool
    comment: str
    idempotency_key: str
    def __init__(self, demand_id: _Optional[str] = ..., stage_key: _Optional[str] = ..., approved: _Optional[bool] = ..., comment: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ListThreadsRequest(_message.Message):
    __slots__ = ("demand_id",)
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    def __init__(self, demand_id: _Optional[str] = ...) -> None: ...

class ListThreadsResponse(_message.Message):
    __slots__ = ("threads",)
    THREADS_FIELD_NUMBER: _ClassVar[int]
    threads: _containers.RepeatedCompositeFieldContainer[Thread]
    def __init__(self, threads: _Optional[_Iterable[_Union[Thread, _Mapping]]] = ...) -> None: ...

class CreateThreadRequest(_message.Message):
    __slots__ = ("demand_id", "key", "card", "idempotency_key")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    CARD_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    key: str
    card: AgentCard
    idempotency_key: str
    def __init__(self, demand_id: _Optional[str] = ..., key: _Optional[str] = ..., card: _Optional[_Union[AgentCard, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class PostMessageRequest(_message.Message):
    __slots__ = ("thread_id", "text", "idempotency_key")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    text: str
    idempotency_key: str
    def __init__(self, thread_id: _Optional[str] = ..., text: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ListFindingsRequest(_message.Message):
    __slots__ = ("demand_id", "thread_id", "page")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    thread_id: str
    page: _common_pb2.PageRequest
    def __init__(self, demand_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., page: _Optional[_Union[_common_pb2.PageRequest, _Mapping]] = ...) -> None: ...

class ListFindingsResponse(_message.Message):
    __slots__ = ("findings", "page")
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    findings: _containers.RepeatedCompositeFieldContainer[Finding]
    page: _common_pb2.PageResponse
    def __init__(self, findings: _Optional[_Iterable[_Union[Finding, _Mapping]]] = ..., page: _Optional[_Union[_common_pb2.PageResponse, _Mapping]] = ...) -> None: ...

class PublishFindingRequest(_message.Message):
    __slots__ = ("demand_id", "thread_id", "title", "payload", "idempotency_key")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    thread_id: str
    title: str
    payload: _struct_pb2.Struct
    idempotency_key: str
    def __init__(self, demand_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., title: _Optional[str] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class WatchDemandRequest(_message.Message):
    __slots__ = ("demand_id",)
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    def __init__(self, demand_id: _Optional[str] = ...) -> None: ...

class DemandEvent(_message.Message):
    __slots__ = ("type", "aggregate", "payload", "at")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    AGGREGATE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    type: str
    aggregate: str
    payload: _struct_pb2.Struct
    at: _timestamp_pb2.Timestamp
    def __init__(self, type: _Optional[str] = ..., aggregate: _Optional[str] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
