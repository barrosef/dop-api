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

class Role(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ROLE_UNSPECIFIED: _ClassVar[Role]
    ROLE_OWNER: _ClassVar[Role]
    ROLE_ADMIN: _ClassVar[Role]
    ROLE_DEVELOPER: _ClassVar[Role]
    ROLE_VIEWER: _ClassVar[Role]
ROLE_UNSPECIFIED: Role
ROLE_OWNER: Role
ROLE_ADMIN: Role
ROLE_DEVELOPER: Role
ROLE_VIEWER: Role

class User(_message.Message):
    __slots__ = ("id", "email", "name", "avatar_url", "providers", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    AVATAR_URL_FIELD_NUMBER: _ClassVar[int]
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    email: str
    name: str
    avatar_url: str
    providers: _containers.RepeatedScalarFieldContainer[str]
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., email: _Optional[str] = ..., name: _Optional[str] = ..., avatar_url: _Optional[str] = ..., providers: _Optional[_Iterable[str]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class Account(_message.Message):
    __slots__ = ("id", "kind", "handle", "display_name", "legal_id", "legal_name", "verified_domain", "audit")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[Account.Kind]
        KIND_PERSONAL: _ClassVar[Account.Kind]
        KIND_ORGANIZATION: _ClassVar[Account.Kind]
    KIND_UNSPECIFIED: Account.Kind
    KIND_PERSONAL: Account.Kind
    KIND_ORGANIZATION: Account.Kind
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    LEGAL_ID_FIELD_NUMBER: _ClassVar[int]
    LEGAL_NAME_FIELD_NUMBER: _ClassVar[int]
    VERIFIED_DOMAIN_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: Account.Kind
    handle: str
    display_name: str
    legal_id: str
    legal_name: str
    verified_domain: str
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[_Union[Account.Kind, str]] = ..., handle: _Optional[str] = ..., display_name: _Optional[str] = ..., legal_id: _Optional[str] = ..., legal_name: _Optional[str] = ..., verified_domain: _Optional[str] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class Membership(_message.Message):
    __slots__ = ("id", "user", "account", "role", "audit")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    user: _common_pb2.UserRef
    account: _common_pb2.AccountRef
    role: Role
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., user: _Optional[_Union[_common_pb2.UserRef, _Mapping]] = ..., account: _Optional[_Union[_common_pb2.AccountRef, _Mapping]] = ..., role: _Optional[_Union[Role, str]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class Invite(_message.Message):
    __slots__ = ("id", "account", "email", "role", "grants", "status", "expires_at", "audit")
    class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATUS_UNSPECIFIED: _ClassVar[Invite.Status]
        STATUS_PENDING: _ClassVar[Invite.Status]
        STATUS_ACCEPTED: _ClassVar[Invite.Status]
        STATUS_EXPIRED: _ClassVar[Invite.Status]
        STATUS_REVOKED: _ClassVar[Invite.Status]
    STATUS_UNSPECIFIED: Invite.Status
    STATUS_PENDING: Invite.Status
    STATUS_ACCEPTED: Invite.Status
    STATUS_EXPIRED: Invite.Status
    STATUS_REVOKED: Invite.Status
    ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    GRANTS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    AUDIT_FIELD_NUMBER: _ClassVar[int]
    id: str
    account: _common_pb2.AccountRef
    email: str
    role: Role
    grants: _containers.RepeatedCompositeFieldContainer[ResourceGrantSpec]
    status: Invite.Status
    expires_at: _timestamp_pb2.Timestamp
    audit: _common_pb2.AuditStamp
    def __init__(self, id: _Optional[str] = ..., account: _Optional[_Union[_common_pb2.AccountRef, _Mapping]] = ..., email: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ..., grants: _Optional[_Iterable[_Union[ResourceGrantSpec, _Mapping]]] = ..., status: _Optional[_Union[Invite.Status, str]] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., audit: _Optional[_Union[_common_pb2.AuditStamp, _Mapping]] = ...) -> None: ...

class ResourceGrantSpec(_message.Message):
    __slots__ = ("resource", "level")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    resource: _common_pb2.ResourceRef
    level: str
    def __init__(self, resource: _Optional[_Union[_common_pb2.ResourceRef, _Mapping]] = ..., level: _Optional[str] = ...) -> None: ...

class ListInvitesRequest(_message.Message):
    __slots__ = ("ctx",)
    CTX_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ...) -> None: ...

class ListInvitesResponse(_message.Message):
    __slots__ = ("invites",)
    INVITES_FIELD_NUMBER: _ClassVar[int]
    invites: _containers.RepeatedCompositeFieldContainer[Invite]
    def __init__(self, invites: _Optional[_Iterable[_Union[Invite, _Mapping]]] = ...) -> None: ...

class GetInviteRequest(_message.Message):
    __slots__ = ("ctx", "id")
    CTX_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    id: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., id: _Optional[str] = ...) -> None: ...

class InvitePreview(_message.Message):
    __slots__ = ("id", "account_name", "role", "status", "expires_at", "usable")
    ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_NAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    USABLE_FIELD_NUMBER: _ClassVar[int]
    id: str
    account_name: str
    role: Role
    status: Invite.Status
    expires_at: _timestamp_pb2.Timestamp
    usable: bool
    def __init__(self, id: _Optional[str] = ..., account_name: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ..., status: _Optional[_Union[Invite.Status, str]] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., usable: _Optional[bool] = ...) -> None: ...

class GetUserRequest(_message.Message):
    __slots__ = ("ctx", "id")
    CTX_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    id: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., id: _Optional[str] = ...) -> None: ...

class EnsureUserRequest(_message.Message):
    __slots__ = ("subject", "email", "email_verified", "name", "avatar_url", "provider", "idempotency_key")
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    EMAIL_VERIFIED_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    AVATAR_URL_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    subject: str
    email: str
    email_verified: bool
    name: str
    avatar_url: str
    provider: str
    idempotency_key: str
    def __init__(self, subject: _Optional[str] = ..., email: _Optional[str] = ..., email_verified: _Optional[bool] = ..., name: _Optional[str] = ..., avatar_url: _Optional[str] = ..., provider: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ListAccountsRequest(_message.Message):
    __slots__ = ("ctx", "user")
    CTX_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    user: _common_pb2.UserRef
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., user: _Optional[_Union[_common_pb2.UserRef, _Mapping]] = ...) -> None: ...

class ListAccountsResponse(_message.Message):
    __slots__ = ("accounts",)
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    accounts: _containers.RepeatedCompositeFieldContainer[Account]
    def __init__(self, accounts: _Optional[_Iterable[_Union[Account, _Mapping]]] = ...) -> None: ...

class GetAccountRequest(_message.Message):
    __slots__ = ("ctx", "id")
    CTX_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    id: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., id: _Optional[str] = ...) -> None: ...

class CreateAccountRequest(_message.Message):
    __slots__ = ("ctx", "kind", "handle", "display_name", "legal_id", "idempotency_key")
    CTX_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    LEGAL_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    kind: Account.Kind
    handle: str
    display_name: str
    legal_id: str
    idempotency_key: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., kind: _Optional[_Union[Account.Kind, str]] = ..., handle: _Optional[str] = ..., display_name: _Optional[str] = ..., legal_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ListMembershipsRequest(_message.Message):
    __slots__ = ("ctx", "account")
    CTX_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    account: _common_pb2.AccountRef
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., account: _Optional[_Union[_common_pb2.AccountRef, _Mapping]] = ...) -> None: ...

class ListMembershipsResponse(_message.Message):
    __slots__ = ("memberships",)
    MEMBERSHIPS_FIELD_NUMBER: _ClassVar[int]
    memberships: _containers.RepeatedCompositeFieldContainer[Membership]
    def __init__(self, memberships: _Optional[_Iterable[_Union[Membership, _Mapping]]] = ...) -> None: ...

class CreateInviteRequest(_message.Message):
    __slots__ = ("ctx", "email", "role", "grants", "idempotency_key")
    CTX_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    GRANTS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    email: str
    role: Role
    grants: _containers.RepeatedCompositeFieldContainer[ResourceGrantSpec]
    idempotency_key: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., email: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ..., grants: _Optional[_Iterable[_Union[ResourceGrantSpec, _Mapping]]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class AcceptInviteRequest(_message.Message):
    __slots__ = ("ctx", "invite_id", "idempotency_key")
    CTX_FIELD_NUMBER: _ClassVar[int]
    INVITE_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    invite_id: str
    idempotency_key: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., invite_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class RevokeInviteRequest(_message.Message):
    __slots__ = ("ctx", "id")
    CTX_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    id: str
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., id: _Optional[str] = ...) -> None: ...

class UpdateMembershipRequest(_message.Message):
    __slots__ = ("ctx", "membership_id", "role", "grants")
    CTX_FIELD_NUMBER: _ClassVar[int]
    MEMBERSHIP_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    GRANTS_FIELD_NUMBER: _ClassVar[int]
    ctx: _common_pb2.CallContext
    membership_id: str
    role: Role
    grants: _containers.RepeatedCompositeFieldContainer[ResourceGrantSpec]
    def __init__(self, ctx: _Optional[_Union[_common_pb2.CallContext, _Mapping]] = ..., membership_id: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ..., grants: _Optional[_Iterable[_Union[ResourceGrantSpec, _Mapping]]] = ...) -> None: ...
