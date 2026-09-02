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

class SecondFactorKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SECOND_FACTOR_KIND_UNSPECIFIED: _ClassVar[SecondFactorKind]
    SECOND_FACTOR_KIND_TOTP: _ClassVar[SecondFactorKind]
    SECOND_FACTOR_KIND_EMAIL: _ClassVar[SecondFactorKind]
    SECOND_FACTOR_KIND_SMS: _ClassVar[SecondFactorKind]
SECOND_FACTOR_KIND_UNSPECIFIED: SecondFactorKind
SECOND_FACTOR_KIND_TOTP: SecondFactorKind
SECOND_FACTOR_KIND_EMAIL: SecondFactorKind
SECOND_FACTOR_KIND_SMS: SecondFactorKind

class SecondFactor(_message.Message):
    __slots__ = ("id", "kind", "status", "label", "masked_destination", "confirmed_at", "last_used_at", "audit")
    class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATUS_UNSPECIFIED: _ClassVar[SecondFactor.Status]
        STATUS_PENDING: _ClassVar[SecondFactor.Status]
        STATUS_ACTIVE: _ClassVar[SecondFactor.Status]
        STATUS_REVOKED: _ClassVar[SecondFactor.Status]
    STATUS_UNSPECIFIED: SecondFactor.Status
    STATUS_PENDING: SecondFactor.Status
    STATUS_ACTIVE: SecondFactor.Status
    STATUS_REVOKED: SecondFactor.Status
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    MASKED_DESTINATION_FIELD_NUMBER: _ClassVar[int]
    CONFIRMED_AT_FIELD_NUMBER: _ClassVar[int]
    LAST_USED_AT_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: SecondFactorKind
    status: SecondFactor.Status
    label: str
    masked_destination: str
    confirmed_at: _timestamp_pb2.Timestamp
    last_used_at: _timestamp_pb2.Timestamp
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[_Union[SecondFactorKind, str]] = ..., status: _Optional[_Union[SecondFactor.Status, str]] = ..., label: _Optional[str] = ..., masked_destination: _Optional[str] = ..., confirmed_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_used_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class EnrollSecondFactorRequest(_message.Message):
    __slots__ = ("ctx", "kind", "label", "destination", "idempotency_key")
    CTX_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    kind: SecondFactorKind
    label: str
    destination: str
    idempotency_key: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., kind: _Optional[_Union[SecondFactorKind, str]] = ..., label: _Optional[str] = ..., destination: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class EnrollSecondFactorResponse(_message.Message):
    __slots__ = ("factor", "challenge_id", "secret", "uri")
    FACTOR_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_ID_FIELD_NUMBER: _ClassVar[int]
    SECRET_FIELD_NUMBER: _ClassVar[int]
    URI_FIELD_NUMBER: _ClassVar[int]
    factor: SecondFactor
    challenge_id: str
    secret: str
    uri: str
    def __init__(self, factor: _Optional[_Union[SecondFactor, _Mapping]] = ..., challenge_id: _Optional[str] = ..., secret: _Optional[str] = ..., uri: _Optional[str] = ...) -> None: ...

class ConfirmSecondFactorRequest(_message.Message):
    __slots__ = ("ctx", "factor_id", "challenge_id", "code")
    CTX_FIELD_NUMBER: _ClassVar[int]
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_ID_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    factor_id: str
    challenge_id: str
    code: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., factor_id: _Optional[str] = ..., challenge_id: _Optional[str] = ..., code: _Optional[str] = ...) -> None: ...

class ConfirmSecondFactorResponse(_message.Message):
    __slots__ = ("factor", "recovery_codes")
    FACTOR_FIELD_NUMBER: _ClassVar[int]
    RECOVERY_CODES_FIELD_NUMBER: _ClassVar[int]
    factor: SecondFactor
    recovery_codes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, factor: _Optional[_Union[SecondFactor, _Mapping]] = ..., recovery_codes: _Optional[_Iterable[str]] = ...) -> None: ...

class RevokeSecondFactorRequest(_message.Message):
    __slots__ = ("ctx", "id")
    CTX_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    id: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., id: _Optional[str] = ...) -> None: ...

class ListSecondFactorsRequest(_message.Message):
    __slots__ = ("ctx",)
    CTX_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ...) -> None: ...

class ListSecondFactorsResponse(_message.Message):
    __slots__ = ("factors",)
    FACTORS_FIELD_NUMBER: _ClassVar[int]
    factors: _containers.RepeatedCompositeFieldContainer[SecondFactor]
    def __init__(self, factors: _Optional[_Iterable[_Union[SecondFactor, _Mapping]]] = ...) -> None: ...

class ChallengeSecondFactorRequest(_message.Message):
    __slots__ = ("ctx", "factor_id")
    CTX_FIELD_NUMBER: _ClassVar[int]
    FACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    factor_id: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., factor_id: _Optional[str] = ...) -> None: ...

class ChallengeSecondFactorResponse(_message.Message):
    __slots__ = ("challenge_id", "kind", "masked_destination", "expires_at")
    CHALLENGE_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    MASKED_DESTINATION_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    challenge_id: str
    kind: SecondFactorKind
    masked_destination: str
    expires_at: _timestamp_pb2.Timestamp
    def __init__(self, challenge_id: _Optional[str] = ..., kind: _Optional[_Union[SecondFactorKind, str]] = ..., masked_destination: _Optional[str] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class VerifySecondFactorRequest(_message.Message):
    __slots__ = ("ctx", "challenge_id", "code")
    CTX_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_ID_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    challenge_id: str
    code: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., challenge_id: _Optional[str] = ..., code: _Optional[str] = ...) -> None: ...

class VerifyRecoveryCodeRequest(_message.Message):
    __slots__ = ("ctx", "code")
    CTX_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    code: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., code: _Optional[str] = ...) -> None: ...

class StepUp(_message.Message):
    __slots__ = ("method", "recovery", "verified_at", "expires_at")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    RECOVERY_FIELD_NUMBER: _ClassVar[int]
    VERIFIED_AT_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    method: SecondFactorKind
    recovery: bool
    verified_at: _timestamp_pb2.Timestamp
    expires_at: _timestamp_pb2.Timestamp
    def __init__(self, method: _Optional[_Union[SecondFactorKind, str]] = ..., recovery: _Optional[bool] = ..., verified_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class GetSecondFactorStateRequest(_message.Message):
    __slots__ = ("ctx",)
    CTX_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ...) -> None: ...

class GetSecondFactorStateResponse(_message.Message):
    __slots__ = ("required", "enrolled", "stepped_up", "needs_setup", "allowed", "factors", "recovery_codes_left", "step_up_expires_at")
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    ENROLLED_FIELD_NUMBER: _ClassVar[int]
    STEPPED_UP_FIELD_NUMBER: _ClassVar[int]
    NEEDS_SETUP_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_FIELD_NUMBER: _ClassVar[int]
    FACTORS_FIELD_NUMBER: _ClassVar[int]
    RECOVERY_CODES_LEFT_FIELD_NUMBER: _ClassVar[int]
    STEP_UP_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    required: bool
    enrolled: bool
    stepped_up: bool
    needs_setup: bool
    allowed: _containers.RepeatedScalarFieldContainer[SecondFactorKind]
    factors: _containers.RepeatedCompositeFieldContainer[SecondFactor]
    recovery_codes_left: int
    step_up_expires_at: _timestamp_pb2.Timestamp
    def __init__(self, required: _Optional[bool] = ..., enrolled: _Optional[bool] = ..., stepped_up: _Optional[bool] = ..., needs_setup: _Optional[bool] = ..., allowed: _Optional[_Iterable[_Union[SecondFactorKind, str]]] = ..., factors: _Optional[_Iterable[_Union[SecondFactor, _Mapping]]] = ..., recovery_codes_left: _Optional[int] = ..., step_up_expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RegenerateRecoveryCodesRequest(_message.Message):
    __slots__ = ("ctx",)
    CTX_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ...) -> None: ...

class RegenerateRecoveryCodesResponse(_message.Message):
    __slots__ = ("codes",)
    CODES_FIELD_NUMBER: _ClassVar[int]
    codes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, codes: _Optional[_Iterable[str]] = ...) -> None: ...
