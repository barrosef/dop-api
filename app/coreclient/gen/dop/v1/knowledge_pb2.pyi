from app.coreclient.gen.dop.v1 import common_pb2 as _common_pb2
from app.coreclient.gen.dop.v1 import demand_pb2 as _demand_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class KnowledgeArtifact(_message.Message):
    __slots__ = ("id", "project", "kind", "name", "version", "object_ref", "meta", "audit")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[KnowledgeArtifact.Kind]
        KIND_RULE: _ClassVar[KnowledgeArtifact.Kind]
        KIND_INDEX: _ClassVar[KnowledgeArtifact.Kind]
        KIND_MEMORY: _ClassVar[KnowledgeArtifact.Kind]
    KIND_UNSPECIFIED: KnowledgeArtifact.Kind
    KIND_RULE: KnowledgeArtifact.Kind
    KIND_INDEX: KnowledgeArtifact.Kind
    KIND_MEMORY: KnowledgeArtifact.Kind
    ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    OBJECT_REF_FIELD_NUMBER: _ClassVar[int]
    META_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    project: _common_pb2.ProjectRef
    kind: KnowledgeArtifact.Kind
    name: str
    version: int
    object_ref: str
    meta: _struct_pb2.Struct
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., project: _Optional[_Union[_common_pb2.ProjectRef, _Mapping]] = ..., kind: _Optional[_Union[KnowledgeArtifact.Kind, str]] = ..., name: _Optional[str] = ..., version: _Optional[int] = ..., object_ref: _Optional[str] = ..., meta: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class ContextPackage(_message.Message):
    __slots__ = ("demand", "rules", "index", "memories", "findings", "estimated_tokens", "dropped")
    class DroppedEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    DEMAND_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    MEMORIES_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_TOKENS_FIELD_NUMBER: _ClassVar[int]
    DROPPED_FIELD_NUMBER: _ClassVar[int]
    demand: _common_pb2.DemandRef
    rules: _containers.RepeatedScalarFieldContainer[str]
    index: _containers.RepeatedCompositeFieldContainer[KnowledgeArtifact]
    memories: _containers.RepeatedCompositeFieldContainer[KnowledgeArtifact]
    findings: _containers.RepeatedCompositeFieldContainer[_demand_pb2.Finding]
    estimated_tokens: int
    dropped: _containers.ScalarMap[str, int]
    def __init__(self, demand: _Optional[_Union[_common_pb2.DemandRef, _Mapping]] = ..., rules: _Optional[_Iterable[str]] = ..., index: _Optional[_Iterable[_Union[KnowledgeArtifact, _Mapping]]] = ..., memories: _Optional[_Iterable[_Union[KnowledgeArtifact, _Mapping]]] = ..., findings: _Optional[_Iterable[_Union[_demand_pb2.Finding, _Mapping]]] = ..., estimated_tokens: _Optional[int] = ..., dropped: _Optional[_Mapping[str, int]] = ...) -> None: ...

class BuildContextPackageRequest(_message.Message):
    __slots__ = ("demand_id",)
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    def __init__(self, demand_id: _Optional[str] = ...) -> None: ...

class SearchMemoryRequest(_message.Message):
    __slots__ = ("project", "query", "limit")
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    project: _common_pb2.ProjectRef
    query: str
    limit: int
    def __init__(self, project: _Optional[_Union[_common_pb2.ProjectRef, _Mapping]] = ..., query: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class SearchMemoryResponse(_message.Message):
    __slots__ = ("artifacts", "scores")
    ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    SCORES_FIELD_NUMBER: _ClassVar[int]
    artifacts: _containers.RepeatedCompositeFieldContainer[KnowledgeArtifact]
    scores: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, artifacts: _Optional[_Iterable[_Union[KnowledgeArtifact, _Mapping]]] = ..., scores: _Optional[_Iterable[float]] = ...) -> None: ...

class ReadIndexRequest(_message.Message):
    __slots__ = ("project", "repo")
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    REPO_FIELD_NUMBER: _ClassVar[int]
    project: _common_pb2.ProjectRef
    repo: str
    def __init__(self, project: _Optional[_Union[_common_pb2.ProjectRef, _Mapping]] = ..., repo: _Optional[str] = ...) -> None: ...

class PutArtifactRequest(_message.Message):
    __slots__ = ("artifact", "content", "idempotency_key")
    ARTIFACT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    artifact: KnowledgeArtifact
    content: bytes
    idempotency_key: str
    def __init__(self, artifact: _Optional[_Union[KnowledgeArtifact, _Mapping]] = ..., content: _Optional[bytes] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ListRulesRequest(_message.Message):
    __slots__ = ("project",)
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    project: _common_pb2.ProjectRef
    def __init__(self, project: _Optional[_Union[_common_pb2.ProjectRef, _Mapping]] = ...) -> None: ...

class ListRulesResponse(_message.Message):
    __slots__ = ("rules",)
    RULES_FIELD_NUMBER: _ClassVar[int]
    rules: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, rules: _Optional[_Iterable[str]] = ...) -> None: ...
