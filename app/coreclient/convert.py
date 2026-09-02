"""Translation between the contract's vocabulary (.proto) and the BFF's.

A proto enum is a number; the edge speaks strings. The conversion lives here, in
one place only, so the router and the resolver never disagree about what
"owner" is.
"""

from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2

# The four predefined roles (dop-core: identity.Role).
ROLE_TO_NAME: dict[int, str] = {
    identity_pb2.ROLE_OWNER: "owner",
    identity_pb2.ROLE_ADMIN: "admin",
    identity_pb2.ROLE_DEVELOPER: "developer",
    identity_pb2.ROLE_VIEWER: "viewer",
}
NAME_TO_ROLE: dict[str, int] = {name: value for value, name in ROLE_TO_NAME.items()}

KIND_TO_NAME: dict[int, str] = {
    identity_pb2.Account.KIND_PERSONAL: "personal",
    identity_pb2.Account.KIND_ORGANIZATION: "organization",
}

INVITE_STATUS_TO_NAME: dict[int, str] = {
    identity_pb2.Invite.STATUS_PENDING: "pending",
    identity_pb2.Invite.STATUS_ACCEPTED: "accepted",
    identity_pb2.Invite.STATUS_EXPIRED: "expired",
    identity_pb2.Invite.STATUS_REVOKED: "revoked",
}


def role_name(role: int) -> str:
    return ROLE_TO_NAME.get(role, "")


def role_value(name: str) -> int:
    return NAME_TO_ROLE.get(name.lower(), identity_pb2.ROLE_UNSPECIFIED)


def account_kind_name(kind: int) -> str:
    return KIND_TO_NAME.get(kind, "")


def invite_status_name(status: int) -> str:
    return INVITE_STATUS_TO_NAME.get(status, "")


# The actor's kind ↔ enum. The core reads the kind from the METADATA
# (`x-actor-kind`), not from here — but keeping the two consistent stops anybody
# from reading the body and concluding the wrong thing about who acted.
ACTOR_KIND_BY_NAME = {
    "user": common_pb2.ActorRef.KIND_USER,
    "agent": common_pb2.ActorRef.KIND_AGENT,
    "subagent": common_pb2.ActorRef.KIND_SUBAGENT,
    "system": common_pb2.ActorRef.KIND_SYSTEM,
}


# call_context / call_context_from USED TO LIVE HERE.
#
# They built the `CallContext ctx = 1` that every request carried and the core
# ignored: authorization reads the metadata (ADR-0017, convention 5). The field
# left the contract, and with it the only reason to build one.
#
# What they carried that mattered — WHO, in WHICH account, and above all the
# actor's KIND — already travels in `metadata_for`: `x-actor-id`,
# `x-account-id`, `x-actor-kind` and `x-session-id`. The kind is the one worth
# naming here: recording an agent's answer as the human's speech corrupts the
# event log, which is the demand's truth (ADR-0006).
