"""Identity routes — an HTTP adapter over the use cases.

What this module does: receive HTTP, call `app/usecases/identity.py`, return
JSON. What it does NOT do: decide anything. The rule, the authorization and the
conversation with the core live in the use case, which the gRPC port calls in
exactly the same way (`app/grpcapi/identity.py`) — that is how the two
transports cannot diverge.

The decorators (@log, @account_scoped, @require_role) are there too, not here:
authorization pinned to the router would hold for REST alone.
"""

from fastapi import APIRouter

from app.usecases import identity as uc
from app.usecases.identity import (
    AccountSummary,
    GrantSpec,
    InviteSummary,
    MemberSummary,
    MeResponse,
    NewAccount,
    NewInvite,
)

# Re-exported for whoever already imported the models from here — they are the
# edge's DTOs, shared with gRPC, and now live in the use case.
__all__ = [
    "AccountSummary",
    "GrantSpec",
    "InviteSummary",
    "MeResponse",
    "MemberSummary",
    "NewAccount",
    "NewInvite",
    "router",
]

router = APIRouter(prefix="/api/v1", tags=["identity"])


@router.get("/me", response_model=MeResponse)
async def me() -> MeResponse:
    """Who I am, in the active account."""
    return await uc.me()


@router.get("/accounts", response_model=list[AccountSummary])
async def list_accounts() -> list[AccountSummary]:
    """The user's accounts — it feeds the cockpit's active-account selector."""
    return await uc.list_accounts()


@router.post("/accounts", response_model=AccountSummary, status_code=201)
async def create_account(body: NewAccount) -> AccountSummary:
    """Creates an organization.

    REST has nowhere for the client to carry the idempotency key, so the use
    case generates one. In gRPC the client may send its own.
    """
    return await uc.create_account(body)


@router.get("/accounts/current/members", response_model=list[MemberSummary])
async def list_members() -> list[MemberSummary]:
    """The active account's members — it requires a selected account AND a management role."""
    return await uc.list_members()


@router.post("/invites", response_model=InviteSummary, status_code=201)
async def create_invite(body: NewInvite) -> InviteSummary:
    """Invites somebody to the active account."""
    return await uc.create_invite(body)
