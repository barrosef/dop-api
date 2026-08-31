"""Contexto de requisição — preenchido UMA vez por middleware, lido por decorators.

O padrão: o handler não recebe parâmetro de auth nem de log; o transversal fica
invisível no código de negócio. É o que mantém os routers legíveis.
"""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class Principal:
    """Resultado NORMALIZADO da verificação de token.

    Claims de Firebase não passam daqui — é o que permite trocar o provedor de
    identidade sem tocar no resto (ADR-0001).
    """

    subject: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    avatar_url: str = ""
    providers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuthContext:
    """Quem está chamando, em qual conta, com quais permissões."""

    principal: Principal
    user_id: str = ""
    account_id: str = ""
    role: str = ""
    # recurso -> nível (use | manage), resolvido pelo core
    grants: dict[str, str] = field(default_factory=dict)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def grant_level(self, resource_id: str) -> str | None:
        # owner e admin têm manage implícito em todo recurso — sem isso ninguém
        # consegue consertar uma integração quebrada.
        if self.role in ("owner", "admin"):
            return "manage"
        return self.grants.get(resource_id)


# Populados por LoggingMiddleware e AuthMiddleware, nessa ordem.
# Default IMUTÁVEL: um dict vazio compartilhado entre contextos é um bug à
# espera de acontecer — quem escrevesse nele contaminaria todas as requisições.
_SEM_CONTEXTO: Mapping[str, str] = MappingProxyType({})

request_ctx: ContextVar[Mapping[str, str]] = ContextVar("request_ctx", default=_SEM_CONTEXTO)
auth_ctx: ContextVar[AuthContext | None] = ContextVar("auth_ctx", default=None)


def current_request_id() -> str:
    return request_ctx.get().get("request_id", "")


def current_account_id() -> str:
    ctx = auth_ctx.get()
    return ctx.account_id if ctx else ""
