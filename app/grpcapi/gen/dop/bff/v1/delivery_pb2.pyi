from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Reviewer(_message.Message):
    __slots__ = ("name", "initials", "status")
    NAME_FIELD_NUMBER: _ClassVar[int]
    INITIALS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    name: str
    initials: str
    status: str
    def __init__(self, name: _Optional[str] = ..., initials: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class PullRequest(_message.Message):
    __slots__ = ("id", "demand_id", "repo", "source_branch", "target_branch", "url", "merged", "has_conflict", "reviewers", "pending_reviews")
    ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    REPO_FIELD_NUMBER: _ClassVar[int]
    SOURCE_BRANCH_FIELD_NUMBER: _ClassVar[int]
    TARGET_BRANCH_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    MERGED_FIELD_NUMBER: _ClassVar[int]
    HAS_CONFLICT_FIELD_NUMBER: _ClassVar[int]
    REVIEWERS_FIELD_NUMBER: _ClassVar[int]
    PENDING_REVIEWS_FIELD_NUMBER: _ClassVar[int]
    id: str
    demand_id: str
    repo: str
    source_branch: str
    target_branch: str
    url: str
    merged: bool
    has_conflict: bool
    reviewers: _containers.RepeatedCompositeFieldContainer[Reviewer]
    pending_reviews: int
    def __init__(self, id: _Optional[str] = ..., demand_id: _Optional[str] = ..., repo: _Optional[str] = ..., source_branch: _Optional[str] = ..., target_branch: _Optional[str] = ..., url: _Optional[str] = ..., merged: _Optional[bool] = ..., has_conflict: _Optional[bool] = ..., reviewers: _Optional[_Iterable[_Union[Reviewer, _Mapping]]] = ..., pending_reviews: _Optional[int] = ...) -> None: ...

class MergeQueueEntry(_message.Message):
    __slots__ = ("id", "repo_id", "demand_id", "position", "state", "overlapping_files")
    class State(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATE_UNSPECIFIED: _ClassVar[MergeQueueEntry.State]
        STATE_QUEUED: _ClassVar[MergeQueueEntry.State]
        STATE_REBASING: _ClassVar[MergeQueueEntry.State]
        STATE_VERIFYING: _ClassVar[MergeQueueEntry.State]
        STATE_MERGED: _ClassVar[MergeQueueEntry.State]
        STATE_CONFLICT: _ClassVar[MergeQueueEntry.State]
    STATE_UNSPECIFIED: MergeQueueEntry.State
    STATE_QUEUED: MergeQueueEntry.State
    STATE_REBASING: MergeQueueEntry.State
    STATE_VERIFYING: MergeQueueEntry.State
    STATE_MERGED: MergeQueueEntry.State
    STATE_CONFLICT: MergeQueueEntry.State
    ID_FIELD_NUMBER: _ClassVar[int]
    REPO_ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    OVERLAPPING_FILES_FIELD_NUMBER: _ClassVar[int]
    id: str
    repo_id: str
    demand_id: str
    position: int
    state: MergeQueueEntry.State
    overlapping_files: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., repo_id: _Optional[str] = ..., demand_id: _Optional[str] = ..., position: _Optional[int] = ..., state: _Optional[_Union[MergeQueueEntry.State, str]] = ..., overlapping_files: _Optional[_Iterable[str]] = ...) -> None: ...

class Directive(_message.Message):
    __slots__ = ("id", "project_id", "kind", "payload", "decided_by_id", "decided_by_name")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[Directive.Kind]
        KIND_CHERRY_PICK: _ClassVar[Directive.Kind]
        KIND_MERGE_ORDER: _ClassVar[Directive.Kind]
        KIND_FILE_PARTITION: _ClassVar[Directive.Kind]
        KIND_CROSS_VERIFY: _ClassVar[Directive.Kind]
    KIND_UNSPECIFIED: Directive.Kind
    KIND_CHERRY_PICK: Directive.Kind
    KIND_MERGE_ORDER: Directive.Kind
    KIND_FILE_PARTITION: Directive.Kind
    KIND_CROSS_VERIFY: Directive.Kind
    ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    DECIDED_BY_ID_FIELD_NUMBER: _ClassVar[int]
    DECIDED_BY_NAME_FIELD_NUMBER: _ClassVar[int]
    id: str
    project_id: str
    kind: Directive.Kind
    payload: _struct_pb2.Struct
    decided_by_id: str
    decided_by_name: str
    def __init__(self, id: _Optional[str] = ..., project_id: _Optional[str] = ..., kind: _Optional[_Union[Directive.Kind, str]] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., decided_by_id: _Optional[str] = ..., decided_by_name: _Optional[str] = ...) -> None: ...

class MergeRefusal(_message.Message):
    __slots__ = ("reason", "missing")
    REASON_FIELD_NUMBER: _ClassVar[int]
    MISSING_FIELD_NUMBER: _ClassVar[int]
    reason: str
    missing: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, reason: _Optional[str] = ..., missing: _Optional[_Iterable[str]] = ...) -> None: ...

class DeliveryBoard(_message.Message):
    __slots__ = ("pull_requests", "directives")
    PULL_REQUESTS_FIELD_NUMBER: _ClassVar[int]
    DIRECTIVES_FIELD_NUMBER: _ClassVar[int]
    pull_requests: _containers.RepeatedCompositeFieldContainer[PullRequest]
    directives: _containers.RepeatedCompositeFieldContainer[Directive]
    def __init__(self, pull_requests: _Optional[_Iterable[_Union[PullRequest, _Mapping]]] = ..., directives: _Optional[_Iterable[_Union[Directive, _Mapping]]] = ...) -> None: ...

class GetDeliveryBoardRequest(_message.Message):
    __slots__ = ("project_id",)
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    def __init__(self, project_id: _Optional[str] = ...) -> None: ...

class ListPullRequestsRequest(_message.Message):
    __slots__ = ("demand_id", "project_id")
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    demand_id: str
    project_id: str
    def __init__(self, demand_id: _Optional[str] = ..., project_id: _Optional[str] = ...) -> None: ...

class ListPullRequestsResponse(_message.Message):
    __slots__ = ("pull_requests",)
    PULL_REQUESTS_FIELD_NUMBER: _ClassVar[int]
    pull_requests: _containers.RepeatedCompositeFieldContainer[PullRequest]
    def __init__(self, pull_requests: _Optional[_Iterable[_Union[PullRequest, _Mapping]]] = ...) -> None: ...

class GetMergeQueueRequest(_message.Message):
    __slots__ = ("repo_id",)
    REPO_ID_FIELD_NUMBER: _ClassVar[int]
    repo_id: str
    def __init__(self, repo_id: _Optional[str] = ...) -> None: ...

class GetMergeQueueResponse(_message.Message):
    __slots__ = ("entries",)
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    entries: _containers.RepeatedCompositeFieldContainer[MergeQueueEntry]
    def __init__(self, entries: _Optional[_Iterable[_Union[MergeQueueEntry, _Mapping]]] = ...) -> None: ...

class EnqueueMergeRequest(_message.Message):
    __slots__ = ("repo_id", "demand_id", "idempotency_key")
    REPO_ID_FIELD_NUMBER: _ClassVar[int]
    DEMAND_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    repo_id: str
    demand_id: str
    idempotency_key: str
    def __init__(self, repo_id: _Optional[str] = ..., demand_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class EnqueueMergeResponse(_message.Message):
    __slots__ = ("entry", "refusal")
    ENTRY_FIELD_NUMBER: _ClassVar[int]
    REFUSAL_FIELD_NUMBER: _ClassVar[int]
    entry: MergeQueueEntry
    refusal: MergeRefusal
    def __init__(self, entry: _Optional[_Union[MergeQueueEntry, _Mapping]] = ..., refusal: _Optional[_Union[MergeRefusal, _Mapping]] = ...) -> None: ...

class ListDirectivesRequest(_message.Message):
    __slots__ = ("project_id",)
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    def __init__(self, project_id: _Optional[str] = ...) -> None: ...

class ListDirectivesResponse(_message.Message):
    __slots__ = ("directives",)
    DIRECTIVES_FIELD_NUMBER: _ClassVar[int]
    directives: _containers.RepeatedCompositeFieldContainer[Directive]
    def __init__(self, directives: _Optional[_Iterable[_Union[Directive, _Mapping]]] = ...) -> None: ...

class DecideDirectiveRequest(_message.Message):
    __slots__ = ("directive_id", "decision", "idempotency_key")
    DIRECTIVE_ID_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    directive_id: str
    decision: _struct_pb2.Struct
    idempotency_key: str
    def __init__(self, directive_id: _Optional[str] = ..., decision: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...
