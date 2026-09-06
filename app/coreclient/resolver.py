"""The authorization resolver — who the user is and what they may do, per the CORE.

It runs once per request, inside AuthMiddleware, between verifying the token and
running the handler. It returns the triple `(user_id, role, grants)` that makes
up the AuthContext.

The rule this module defends: the BFF does NOT decide permission. It asks. Roles
and grants live in the core, next to the state that justifies them (ADR-0016).
If a role table ever appears here, the boundary has fallen.
"""

from fastapi import HTTPException
from grpc import StatusCode
from grpc.aio import AioRpcError

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import role_name
from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.platform.context import Principal
from app.platform.errors import as_http
from app.platform.logging.config import get_logger
from app.settings import settings

# Failures that must NOT bring the request down while discovering the role: the
# membership has already been proven by ListAccounts. A service not yet
# registered in the core, or a role with no permission to list members, becomes
# an empty role — @require_role refuses later, with a 403, which is the right
# answer.
_TOLERABLE = (
    StatusCode.UNIMPLEMENTED,
    StatusCode.PERMISSION_DENIED,
    StatusCode.NOT_FOUND,
)


class CoreResolver:
    """`resolver(principal, account_id, raw_token="") -> (user_id, role, grants)`."""

    def __init__(self, deadline_s: float | None = None):
        self._deadline = deadline_s if deadline_s is not None else settings.core_deadline_s

    async def __call__(
        self, principal: Principal, account_id: str, raw_token: str = ""
    ) -> tuple[str, str, dict[str, str]]:
        actor_name = principal.name or principal.email
        user_id = await self._ensure_user(principal, raw_token)

        if not account_id:
            # No active account yet is a VALID state: it is how the cockpit
            # loads the account selector right after login. The one that
            # requires an account is @account_scoped, on the route.
            return user_id, "", {}

        md = core.metadata_for(user_id=user_id, account_id=account_id, actor_name=actor_name)

        await self._assert_membership(user_id, account_id, md)
        role = await self._role_in_account(user_id, account_id, md)
        return user_id, role, self._grants()

    async def _ensure_user(self, principal: Principal, raw_token: str = "") -> str:
        """Idempotent by design — it runs on EVERY login, not only the first.

        It carries the person's TOKEN and not only the edge's assertion,
        because since ADR-0029 the core reads who the person is from the
        signature it verified itself rather than from this request's body.
        Without the header the core refuses, and it refuses on every request,
        not only the first.
        """
        req = identity_pb2.EnsureUserRequest(
            subject=principal.subject,
            email=principal.email,
            email_verified=principal.email_verified,
            name=principal.name,
            avatar_url=principal.avatar_url,
            provider=principal.providers[0] if principal.providers else "",
            idempotency_key=core.idempotency_key(),
        )
        try:
            user = await stubs.identity_stub().EnsureUser(
                req, metadata=core.metadata_for(raw_token=raw_token), timeout=self._deadline
            )
        except AioRpcError as exc:
            raise as_http(exc) from exc
        return user.id

    async def _assert_membership(self, user_id, account_id, md) -> None:
        """With no membership in the requested account, the request dies here with a 403."""
        req = identity_pb2.ListAccountsRequest(user=common_pb2.UserRef(id=user_id))
        try:
            resp = await stubs.identity_stub().ListAccounts(
                req, metadata=md, timeout=self._deadline
            )
        except AioRpcError as exc:
            raise as_http(exc) from exc

        if account_id not in {a.id for a in resp.accounts}:
            raise HTTPException(status_code=403, detail="No membership in the requested account")

    async def _role_in_account(self, user_id, account_id, md) -> str:
        req = identity_pb2.ListMembershipsRequest(account=common_pb2.AccountRef(id=account_id))
        try:
            resp = await stubs.identity_stub().ListMemberships(
                req, metadata=md, timeout=self._deadline
            )
        except AioRpcError as exc:
            if exc.code() in _TOLERABLE:
                get_logger().warning(
                    "role not resolved", account_id=account_id, code=str(exc.code())
                )
                return ""
            raise as_http(exc) from exc

        for m in resp.memberships:
            if m.user.id == user_id:
                return role_name(m.role)
        return ""

    @staticmethod
    def _grants() -> dict[str, str]:
        """Resource grants — always empty today, and that is correct.

        `resource.proto` exposes GrantResource/RevokeGrant, but NOT yet a query
        of grants per user; and ResourceService is not even registered in the
        core (internal/app/register.go). Returning empty degrades gracefully:
        owner and admin keep their implicit `manage`
        (AuthContext.grant_level), and developer/viewer get a 403 from
        @require_grant — which is the safe behaviour. When the RPC exists, it is
        this method, and only this method, that changes.
        """
        return {}
