"""Translation between the use case's vocabulary and the edge contract's.

The use cases speak strings ("owner", "pending") because that is what REST
exposes; the gRPC contract speaks enums, because in a typed contract a free
string is a field nobody validates. The conversion lives here, in one place
only.

`dop.bff.v1`'s enum numbers are deliberately the same as `dop.v1`'s — but the
conversion is EXPLICIT all the same. Relying on the coincidence would work today
and break silently the day one of the ends inserted a value.
"""

from app.grpcapi.gen.dop.bff.v1 import identity_pb2 as bff

ROLE_TO_NAME: dict[int, str] = {
    bff.ROLE_OWNER: "owner",
    bff.ROLE_ADMIN: "admin",
    bff.ROLE_DEVELOPER: "developer",
    bff.ROLE_VIEWER: "viewer",
}
NAME_TO_ROLE: dict[str, int] = {name: value for value, name in ROLE_TO_NAME.items()}

KIND_FROM_NAME: dict[str, int] = {
    "personal": bff.AccountSummary.KIND_PERSONAL,
    "organization": bff.AccountSummary.KIND_ORGANIZATION,
}

STATUS_FROM_NAME: dict[str, int] = {
    "pending": bff.InviteSummary.STATUS_PENDING,
    "accepted": bff.InviteSummary.STATUS_ACCEPTED,
    "expired": bff.InviteSummary.STATUS_EXPIRED,
    "revoked": bff.InviteSummary.STATUS_REVOKED,
}


def role_enum(name: str) -> int:
    """An empty role becomes ROLE_UNSPECIFIED — which is the truth: it was not resolved."""
    return NAME_TO_ROLE.get(name, bff.ROLE_UNSPECIFIED)


def role_name(value: int) -> str:
    return ROLE_TO_NAME.get(value, "")


def kind_enum(name: str) -> int:
    return KIND_FROM_NAME.get(name, bff.AccountSummary.KIND_UNSPECIFIED)


def status_enum(name: str) -> int:
    return STATUS_FROM_NAME.get(name, bff.InviteSummary.STATUS_UNSPECIFIED)
