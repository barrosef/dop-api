"""The request context — filled in ONCE by middleware, read by decorators.

The pattern: the handler takes no auth or logging parameter; the cross-cutting
concern stays invisible in the business code. It is what keeps the routers
readable.
"""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class Principal:
    """The NORMALIZED result of verifying a token.

    Firebase claims go no further than here — it is what allows the identity
    provider to be swapped without touching the rest (ADR-0001).
    """

    subject: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    avatar_url: str = ""
    providers: list[str] = field(default_factory=list)
    # session_id identifies THIS sign-in, and it exists for the second factor:
    # the step-up is per (user, session), because two open sessions are two
    # doors and one of them answering must not open the other (ADR-0027 §5).
    #
    # It is DERIVED, not stored: the subject plus the instant the person
    # authenticated, hashed. It survives the token refresh (auth_time does not
    # change within a session) and changes on the next sign-in — which is
    # exactly the life of a session. Deriving it also keeps the BFF stateless,
    # which is the property that lets it be replicated.
    session_id: str = ""


@dataclass(frozen=True)
class AuthContext:
    """Who is calling, in which account, with which permissions."""

    principal: Principal
    # The token the person presented, whole. It is forwarded to the core so the
    # core can verify it ITSELF (ADR-0029) instead of taking our word for who
    # the actor is. It is not a credential of ours and it is never stored.
    raw_token: str = ""
    user_id: str = ""
    account_id: str = ""
    role: str = ""
    # resource -> level (use | manage), resolved by the core
    grants: dict[str, str] = field(default_factory=dict)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def grant_level(self, resource_id: str) -> str | None:
        # owner and admin have implicit manage over every resource — without it
        # nobody can fix a broken integration.
        if self.role in ("owner", "admin"):
            return "manage"
        return self.grants.get(resource_id)


# Populated by LoggingMiddleware and AuthMiddleware, in that order.
# An IMMUTABLE default: an empty dict shared between contexts is a bug waiting to
# happen — whoever wrote into it would contaminate every request.
_NO_CONTEXT: Mapping[str, str] = MappingProxyType({})

request_ctx: ContextVar[Mapping[str, str]] = ContextVar("request_ctx", default=_NO_CONTEXT)
auth_ctx: ContextVar[AuthContext | None] = ContextVar("auth_ctx", default=None)


def current_request_id() -> str:
    return request_ctx.get().get("request_id", "")


def current_account_id() -> str:
    ctx = auth_ctx.get()
    return ctx.account_id if ctx else ""
