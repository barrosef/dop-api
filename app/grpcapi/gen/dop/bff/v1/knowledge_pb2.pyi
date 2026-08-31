from google.protobuf import struct_pb2 as _struct_pb2
from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as _demand_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class KnowledgeKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    KNOWLEDGE_KIND_UNSPECIFIED: _ClassVar[KnowledgeKind]
    KNOWLEDGE_KIND_RULE: _ClassVar[KnowledgeKind]
    KNOWLEDGE_KIND_INDEX: _ClassVar[KnowledgeKind]
    KNOWLEDGE_KIND_MEMORY: _ClassVar[KnowledgeKind]
KNOWLEDGE_KIND_UNSPECIFIED: KnowledgeKind
KNOWLEDGE_KIND_RULE: KnowledgeKind
KNOWLEDGE_KIND_INDEX: KnowledgeKind
KNOWLEDGE_KIND_MEMORY: KnowledgeKind

class KnowledgeArtifact(_message.Message):
    __slots__ = ("id", "kind", "project_id", "name", "version", "object_ref", "meta", "body", "scope")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    OBJECT_REF_FIELD_NUMBER: _ClassVar[int]
    META_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: KnowledgeKind
    project_id: str
    name: str
    version: int
    object_ref: str
    meta: _struct_pb2.Struct
    body: str
    scope: str
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[_Union[KnowledgeKind, str]] = ..., project_id: _Optional[str] = ..., name: _Optional[str] = ..., version: _Optional[int] = ..., object_ref: _Optional[str] = ..., meta: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., body: _Optional[str] = ..., scope: _Optional[str] = ...) -> None: ...

class DroppedCounts(_message.Message):
    __slots__ = ("rules", "findings", "index", "memories", "truncated")
    RULES_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    MEMORIES_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    rules: int
    findings: int
    index: int
    memories: int
    truncated: bool
    def __init__(self, rules: _Optional[int] = ..., findings: _Optional[int] = ..., index: _Optional[int] = ..., memories: _Optional[int] = ..., truncated: _Optional[bool] = ...) -> None: ...

class ContextPackage(_message.Message):
    __slots__ = ("demand_id", "rules", "index", "memories", "findings", "estimated_tokens", "dropped")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    MEMORIES_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_TOKENS_FIELD_NUMBER: _ClassVar[int]
    DROPPED_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    rules: _containers.RepeatedScalarFieldContainer[str]
    index: _containers.RepeatedCompositeFieldContainer[KnowledgeArtifact]
    memories: _containers.RepeatedCompositeFieldContainer[KnowledgeArtifact]
    findings: _containers.RepeatedCompositeFieldContainer[_demand_pb2.Finding]
    estimated_tokens: int
    dropped: DroppedCounts
    def __init__(self, demand_id: _Optional[str] = ..., rules: _Optional[_Iterable[str]] = ..., index: _Optional[_Iterable[_Union[KnowledgeArtifact, _Mapping]]] = ..., memories: _Optional[_Iterable[_Union[KnowledgeArtifact, _Mapping]]] = ..., findings: _Optional[_Iterable[_Union[_demand_pb2.Finding, _Mapping]]] = ..., estimated_tokens: _Optional[int] = ..., dropped: _Optional[_Union[DroppedCounts, _Mapping]] = ...) -> None: ...

class MemoryHit(_message.Message):
    __slots__ = ("artifact", "score")
    ARTIFACT_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    artifact: KnowledgeArtifact
    score: float
    def __init__(self, artifact: _Optional[_Union[KnowledgeArtifact, _Mapping]] = ..., score: _Optional[float] = ...) -> None: ...

class GetContextPackageRequest(_message.Message):
    __slots__ = ("demand_id",)
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    def __init__(self, demand_id: _Optional[str] = ...) -> None: ...

class SearchMemoryRequest(_message.Message):
    __slots__ = ("project_id", "query", "limit")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    query: str
    limit: int
    def __init__(self, project_id: _Optional[str] = ..., query: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class SearchMemoryResponse(_message.Message):
    __slots__ = ("hits",)
    HITS_FIELD_NUMBER: _ClassVar[int]
    hits: _containers.RepeatedCompositeFieldContainer[MemoryHit]
    def __init__(self, hits: _Optional[_Iterable[_Union[MemoryHit, _Mapping]]] = ...) -> None: ...

class ReadIndexRequest(_message.Message):
    __slots__ = ("project_id", "repo")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    REPO_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    repo: str
    def __init__(self, project_id: _Optional[str] = ..., repo: _Optional[str] = ...) -> None: ...

class PutArtifactRequest(_message.Message):
    __slots__ = ("kind", "project_id", "name", "content", "meta", "idempotency_key")
    KIND_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    META_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    kind: KnowledgeKind
    project_id: str
    name: str
    content: bytes
    meta: _struct_pb2.Struct
    idempotency_key: str
    def __init__(self, kind: _Optional[_Union[KnowledgeKind, str]] = ..., project_id: _Optional[str] = ..., name: _Optional[str] = ..., content: _Optional[bytes] = ..., meta: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ListRulesRequest(_message.Message):
    __slots__ = ("project_id",)
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    def __init__(self, project_id: _Optional[str] = ...) -> None: ...

class ListRulesResponse(_message.Message):
    __slots__ = ("rules",)
    RULES_FIELD_NUMBER: _ClassVar[int]
    rules: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, rules: _Optional[_Iterable[str]] = ...) -> None: ...
