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
    AcceptedInvite,
    AccountSummary,
    GrantSpec,
    InvitePreview,
    InviteSummary,
    MemberRole,
    MemberSummary,
    MeResponse,
    NewAccount,
    NewInvite,
)

# Re-exported for whoever already imported the models from here — they are the
# edge's DTOs, shared with gRPC, and now live in the use case.
__all__ = [
    "AcceptedInvite",
    "AccountSummary",
    "GrantSpec",
    "InvitePreview",
    "InviteSummary",
    "MemberRole",
    "MemberSummary",
    "MeResponse",
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


@router.get("/invites", response_model=list[InviteSummary])
async def list_invites() -> list[InviteSummary]:
    """The active account's invites — the history, not only the pending ones."""
    return await uc.list_invites()


@router.get("/invites/{invite_id}", response_model=InvitePreview)
async def get_invite(invite_id: str) -> InvitePreview:
    """The preview of whoever OPENS the e-mail's link.

    It is the one identity route with no active account: whoever opens an invite
    may not be a member of anything yet. It is where `/invites/:id` in the
    cockpit lands, and it is what closes P-32 — until today the link led to a
    404.
    """
    return await uc.get_invite(invite_id)


@router.post("/invites/{invite_id}/accept", response_model=AcceptedInvite)
async def accept_invite(invite_id: str) -> AcceptedInvite:
    """Accepts the invite and returns the account just joined."""
    return await uc.accept_invite(invite_id)


@router.delete("/invites/{invite_id}", response_model=InviteSummary)
async def revoke_invite(invite_id: str) -> InviteSummary:
    """Revokes a pending invite."""
    return await uc.revoke_invite(invite_id)


@router.patch("/members/{membership_id}", response_model=MemberSummary)
async def update_member(membership_id: str, body: MemberRole) -> MemberSummary:
    """Changes a member's role in the active account."""
    return await uc.update_member(membership_id, body)


@router.delete("/members/{membership_id}", status_code=204)
async def remove_member(membership_id: str) -> None:
    """Removes a member from the active account, along with their grants here."""
    await uc.remove_member(membership_id)
