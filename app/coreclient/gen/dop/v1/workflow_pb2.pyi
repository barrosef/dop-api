from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StageType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STAGE_TYPE_UNSPECIFIED: _ClassVar[StageType]
    STAGE_TYPE_CONTEXT: _ClassVar[StageType]
    STAGE_TYPE_SPEC: _ClassVar[StageType]
    STAGE_TYPE_PLAN: _ClassVar[StageType]
    STAGE_TYPE_IMPLEMENTATION: _ClassVar[StageType]
    STAGE_TYPE_TEST: _ClassVar[StageType]
    STAGE_TYPE_HUMAN_VALIDATION: _ClassVar[StageType]
    STAGE_TYPE_FINALIZATION: _ClassVar[StageType]
    STAGE_TYPE_GENERIC: _ClassVar[StageType]

class ArtifactKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ARTIFACT_KIND_UNSPECIFIED: _ClassVar[ArtifactKind]
    ARTIFACT_KIND_DOCUMENT: _ClassVar[ArtifactKind]
    ARTIFACT_KIND_SPEC: _ClassVar[ArtifactKind]
    ARTIFACT_KIND_PLAN: _ClassVar[ArtifactKind]
    ARTIFACT_KIND_TEST_PLAN: _ClassVar[ArtifactKind]
    ARTIFACT_KIND_DIAGRAM: _ClassVar[ArtifactKind]
    ARTIFACT_KIND_REPORT: _ClassVar[ArtifactKind]

class Gate(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GATE_UNSPECIFIED: _ClassVar[Gate]
    GATE_NONE: _ClassVar[Gate]
    GATE_HUMAN: _ClassVar[Gate]
STAGE_TYPE_UNSPECIFIED: StageType
STAGE_TYPE_CONTEXT: StageType
STAGE_TYPE_SPEC: StageType
STAGE_TYPE_PLAN: StageType
STAGE_TYPE_IMPLEMENTATION: StageType
STAGE_TYPE_TEST: StageType
STAGE_TYPE_HUMAN_VALIDATION: StageType
STAGE_TYPE_FINALIZATION: StageType
STAGE_TYPE_GENERIC: StageType
ARTIFACT_KIND_UNSPECIFIED: ArtifactKind
ARTIFACT_KIND_DOCUMENT: ArtifactKind
ARTIFACT_KIND_SPEC: ArtifactKind
ARTIFACT_KIND_PLAN: ArtifactKind
ARTIFACT_KIND_TEST_PLAN: ArtifactKind
ARTIFACT_KIND_DIAGRAM: ArtifactKind
ARTIFACT_KIND_REPORT: ArtifactKind
GATE_UNSPECIFIED: Gate
GATE_NONE: Gate
GATE_HUMAN: Gate

class StageSpec(_message.Message):
    __slots__ = ("key", "name", "type", "artifacts", "gate", "subtypes")
    KEY_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    GATE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPES_FIELD_NUMBER: _ClassVar[int]
    key: str
    name: str
    type: StageType
    artifacts: _containers.RepeatedScalarFieldContainer[ArtifactKind]
    gate: Gate
    subtypes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, key: _Optional[str] = ..., name: _Optional[str] = ..., type: _Optional[_Union[StageType, str]] = ..., artifacts: _Optional[_Iterable[_Union[ArtifactKind, str]]] = ..., gate: _Optional[_Union[Gate, str]] = ..., subtypes: _Optional[_Iterable[str]] = ...) -> None: ...

class Flow(_message.Message):
    __slots__ = ("id", "name", "description", "version", "owner_scope", "owner_id", "stages", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    OWNER_SCOPE_FIELD_NUMBER: _ClassVar[int]
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    STAGES_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    description: str
    version: int
    owner_scope: str
    owner_id: str
    stages: _containers.RepeatedCompositeFieldContainer[StageSpec]
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., version: _Optional[int] = ..., owner_scope: _Optional[str] = ..., owner_id: _Optional[str] = ..., stages: _Optional[_Iterable[_Union[StageSpec, _Mapping]]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class StageOrigin(_message.Message):
    __slots__ = ("key", "scope", "scope_id")
    KEY_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    key: str
    scope: str
    scope_id: str
    def __init__(self, key: _Optional[str] = ..., scope: _Optional[str] = ..., scope_id: _Optional[str] = ...) -> None: ...

class EffectiveFlow(_message.Message):
    __slots__ = ("flow", "resolved_from", "contributors", "origins")
    FLOW_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_FROM_FIELD_NUMBER: _ClassVar[int]
    CONTRIBUTORS_FIELD_NUMBER: _ClassVar[int]
    ORIGINS_FIELD_NUMBER: _ClassVar[int]
    flow: Flow
    resolved_from: str
    contributors: _containers.RepeatedCompositeFieldContainer[ScopeRef]
    origins: _containers.RepeatedCompositeFieldContainer[StageOrigin]
    def __init__(self, flow: _Optional[_Union[Flow, _Mapping]] = ..., resolved_from: _Optional[str] = ..., contributors: _Optional[_Iterable[_Union[ScopeRef, _Mapping]]] = ..., origins: _Optional[_Iterable[_Union[StageOrigin, _Mapping]]] = ...) -> None: ...

class ScopeRef(_message.Message):
    __slots__ = ("scope", "id")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    scope: str
    id: str
    def __init__(self, scope: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class ListFlowsRequest(_message.Message):
    __slots__ = ("owner_scope", "owner_id")
    OWNER_SCOPE_FIELD_NUMBER: _ClassVar[int]
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    owner_scope: str
    owner_id: str
    def __init__(self, owner_scope: _Optional[str] = ..., owner_id: _Optional[str] = ...) -> None: ...

class ListFlowsResponse(_message.Message):
    __slots__ = ("flows",)
    FLOWS_FIELD_NUMBER: _ClassVar[int]
    flows: _containers.RepeatedCompositeFieldContainer[Flow]
    def __init__(self, flows: _Optional[_Iterable[_Union[Flow, _Mapping]]] = ...) -> None: ...

class GetFlowRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class CreateFlowRequest(_message.Message):
    __slots__ = ("flow", "idempotency_key")
    FLOW_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    flow: Flow
    idempotency_key: str
    def __init__(self, flow: _Optional[_Union[Flow, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class UpdateFlowRequest(_message.Message):
    __slots__ = ("flow",)
    FLOW_FIELD_NUMBER: _ClassVar[int]
    flow: Flow
    def __init__(self, flow: _Optional[_Union[Flow, _Mapping]] = ...) -> None: ...

class ValidateFlowRequest(_message.Message):
    __slots__ = ("flow",)
    FLOW_FIELD_NUMBER: _ClassVar[int]
    flow: Flow
    def __init__(self, flow: _Optional[_Union[Flow, _Mapping]] = ...) -> None: ...

class ValidateFlowResponse(_message.Message):
    __slots__ = ("valid", "errors", "warnings")
    VALID_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    valid: bool
    errors: _containers.RepeatedScalarFieldContainer[str]
    warnings: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, valid: _Optional[bool] = ..., errors: _Optional[_Iterable[str]] = ..., warnings: _Optional[_Iterable[str]] = ...) -> None: ...

class ResolveFlowRequest(_message.Message):
    __slots__ = ("scope", "scope_id")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_ID_FIELD_NUMBER: _ClassVar[int]
    scope: str
    scope_id: str
    def __init__(self, scope: _Optional[str] = ..., scope_id: _Optional[str] = ...) -> None: ...

class PromoteFlowRequest(_message.Message):
    __slots__ = ("flow_id", "target_scope", "target_id")
    FLOW_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_SCOPE_FIELD_NUMBER: _ClassVar[int]
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    flow_id: str
    target_scope: str
    target_id: str
    def __init__(self, flow_id: _Optional[str] = ..., target_scope: _Optional[str] = ..., target_id: _Optional[str] = ...) -> None: ...
