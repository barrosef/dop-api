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

class ResourceGrantSpec(_message.Message):
    __slots__ = ("resource_id", "level")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    resource_id: str
    level: str
    def __init__(self, resource_id: _Optional[str] = ..., level: _Optional[str] = ...) -> None: ...

class Me(_message.Message):
    __slots__ = ("subject", "user_id", "email", "name", "providers", "account_id", "role")
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    subject: str
    user_id: str
    email: str
    name: str
    providers: _containers.RepeatedScalarFieldContainer[str]
    account_id: str
    role: Role
    def __init__(self, subject: _Optional[str] = ..., user_id: _Optional[str] = ..., email: _Optional[str] = ..., name: _Optional[str] = ..., providers: _Optional[_Iterable[str]] = ..., account_id: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ...) -> None: ...

class AccountSummary(_message.Message):
    __slots__ = ("id", "kind", "handle", "display_name", "role")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[AccountSummary.Kind]
        KIND_PERSONAL: _ClassVar[AccountSummary.Kind]
        KIND_ORGANIZATION: _ClassVar[AccountSummary.Kind]
    KIND_UNSPECIFIED: AccountSummary.Kind
    KIND_PERSONAL: AccountSummary.Kind
    KIND_ORGANIZATION: AccountSummary.Kind
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: AccountSummary.Kind
    handle: str
    display_name: str
    role: Role
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[_Union[AccountSummary.Kind, str]] = ..., handle: _Optional[str] = ..., display_name: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ...) -> None: ...

class MemberSummary(_message.Message):
    __slots__ = ("id", "user_id", "role")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    role: Role
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ...) -> None: ...

class InviteSummary(_message.Message):
    __slots__ = ("id", "email", "role", "status")
    class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATUS_UNSPECIFIED: _ClassVar[InviteSummary.Status]
        STATUS_PENDING: _ClassVar[InviteSummary.Status]
        STATUS_ACCEPTED: _ClassVar[InviteSummary.Status]
        STATUS_EXPIRED: _ClassVar[InviteSummary.Status]
        STATUS_REVOKED: _ClassVar[InviteSummary.Status]
    STATUS_UNSPECIFIED: InviteSummary.Status
    STATUS_PENDING: InviteSummary.Status
    STATUS_ACCEPTED: InviteSummary.Status
    STATUS_EXPIRED: InviteSummary.Status
    STATUS_REVOKED: InviteSummary.Status
    ID_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    id: str
    email: str
    role: Role
    status: InviteSummary.Status
    def __init__(self, id: _Optional[str] = ..., email: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ..., status: _Optional[_Union[InviteSummary.Status, str]] = ...) -> None: ...

class GetMeRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListAccountsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListAccountsResponse(_message.Message):
    __slots__ = ("accounts",)
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    accounts: _containers.RepeatedCompositeFieldContainer[AccountSummary]
    def __init__(self, accounts: _Optional[_Iterable[_Union[AccountSummary, _Mapping]]] = ...) -> None: ...

class ListMembersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListMembersResponse(_message.Message):
    __slots__ = ("members",)
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    members: _containers.RepeatedCompositeFieldContainer[MemberSummary]
    def __init__(self, members: _Optional[_Iterable[_Union[MemberSummary, _Mapping]]] = ...) -> None: ...

class CreateAccountRequest(_message.Message):
    __slots__ = ("handle", "display_name", "legal_id", "idempotency_key")
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    LEGAL_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    handle: str
    display_name: str
    legal_id: str
    idempotency_key: str
    def __init__(self, handle: _Optional[str] = ..., display_name: _Optional[str] = ..., legal_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CreateInviteRequest(_message.Message):
    __slots__ = ("email", "role", "grants", "idempotency_key")
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    GRANTS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    email: str
    role: Role
    grants: _containers.RepeatedCompositeFieldContainer[ResourceGrantSpec]
    idempotency_key: str
    def __init__(self, email: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ..., grants: _Optional[_Iterable[_Union[ResourceGrantSpec, _Mapping]]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...
