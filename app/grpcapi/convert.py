"""Tradução entre o vocabulário do caso de uso e o do contrato de borda.

Os casos de uso falam string ("owner", "pending") porque é o que o REST expõe;
o contrato gRPC fala enum, porque num contrato tipado string livre é um campo
que ninguém valida. A conversão mora aqui, num lugar só.

Os números dos enums de `dop.bff.v1` são de propósito iguais aos de `dop.v1` —
mas a conversão é EXPLÍCITA mesmo assim. Depender da coincidência funcionaria
hoje e quebraria em silêncio no dia em que uma das pontas inserisse um valor.
"""

from app.grpcapi.gen.dop.bff.v1 import identity_pb2 as bff

ROLE_TO_NAME: dict[int, str] = {
    bff.ROLE_OWNER: "owner",
    bff.ROLE_ADMIN: "admin",
    bff.ROLE_DEVELOPER: "developer",
    bff.ROLE_VIEWER: "viewer",
}
NAME_TO_ROLE: dict[str, int] = {nome: valor for valor, nome in ROLE_TO_NAME.items()}

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


def role_enum(nome: str) -> int:
    """Papel vazio vira ROLE_UNSPECIFIED — que é a verdade: não foi resolvido."""
    return NAME_TO_ROLE.get(nome, bff.ROLE_UNSPECIFIED)


def role_name(valor: int) -> str:
    return ROLE_TO_NAME.get(valor, "")


def kind_enum(nome: str) -> int:
    return KIND_FROM_NAME.get(nome, bff.AccountSummary.KIND_UNSPECIFIED)


def status_enum(nome: str) -> int:
    return STATUS_FROM_NAME.get(nome, bff.InviteSummary.STATUS_UNSPECIFIED)
