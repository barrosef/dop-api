"""Tradução entre o vocabulário do contrato (.proto) e o do BFF.

Enum do proto é número; a borda fala string. A conversão mora aqui, num lugar
só, para que router e resolver nunca discordem sobre o que é "owner".
"""

from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.platform.context import AuthContext

# Os quatro papéis pré-definidos (dop-core: identity.Role).
ROLE_TO_NAME: dict[int, str] = {
    identity_pb2.ROLE_OWNER: "owner",
    identity_pb2.ROLE_ADMIN: "admin",
    identity_pb2.ROLE_DEVELOPER: "developer",
    identity_pb2.ROLE_VIEWER: "viewer",
}
NAME_TO_ROLE: dict[str, int] = {nome: valor for valor, nome in ROLE_TO_NAME.items()}

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


def role_value(nome: str) -> int:
    return NAME_TO_ROLE.get(nome.lower(), identity_pb2.ROLE_UNSPECIFIED)


def account_kind_name(kind: int) -> str:
    return KIND_TO_NAME.get(kind, "")


def invite_status_name(status: int) -> str:
    return INVITE_STATUS_TO_NAME.get(status, "")


def call_context(
    *, user_id: str, account_id: str = "", actor_name: str = ""
) -> common_pb2.CallContext:
    """Contexto obrigatório de toda chamada: quem, em qual conta (ADR-0016)."""
    return common_pb2.CallContext(
        account=common_pb2.AccountRef(id=account_id),
        actor=common_pb2.ActorRef(
            kind=common_pb2.ActorRef.KIND_USER, id=user_id, name=actor_name
        ),
    )


def call_context_from(ctx: AuthContext) -> common_pb2.CallContext:
    return call_context(
        user_id=ctx.user_id,
        account_id=ctx.account_id,
        actor_name=ctx.principal.name or ctx.principal.email,
    )
