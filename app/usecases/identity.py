"""Identity use cases — called by REST and by gRPC, with no duplication.

These functions are the ONLY place the rule lives. `app/routers/identity.py`
translates HTTP into them; `app/grpcapi/identity.py` translates protobuf into
the same functions. Neither end repeats a single line of decision.

The cross-cutting concerns live here too, not in the adapters: `@log`,
`@account_scoped` and `@require_role` decorate the USE CASE, and therefore hold
equally on both transports. If authorization sat in the router, the gRPC door
would be born open.

The response models are plain Pydantic — no FastAPI — precisely so the gRPC
servicer can consume them without dragging the web framework along.
"""

import logging

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import (
    account_kind_name,
    invite_status_name,
    role_name,
    role_value,
)
from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.platform.security.firebase_admin import (
    FirebaseAdmin,
    LinkNotGenerated,
    on_our_domain,
)
from app.settings import settings


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    """The client's idempotency key, or one of ours (ADR-0013).

    The gRPC contract lets the client send its own — it is the one that knows
    whether it is retrying or asking for something else. REST has nowhere to
    carry it, so we generate one: it protects against the channel's retry, which
    is already the common case.
    """
    return key or core.idempotency_key()


# ── the edge's models ───────────────────────────────────────────────────────


class MeResponse(BaseModel):
    subject: str
    user_id: str
    email: str
    name: str
    providers: list[str]
    account_id: str
    role: str


class AccountSummary(BaseModel):
    id: str
    handle: str
    display_name: str
    kind: str
    role: str


class VerificationRequested(BaseModel):
    """What came back is the address it went to, and nothing else.

    Deliberately not "whether the account exists": answering that would turn
    this endpoint into a way of asking whether an address is registered here.
    """

    email: str


class NewAccount(BaseModel):
    handle: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    legal_id: str = ""  # the company's registration number


class MemberSummary(BaseModel):
    id: str
    user_id: str
    role: str


class GrantSpec(BaseModel):
    resource_id: str
    level: str = "use"  # use | manage


class NewInvite(BaseModel):
    email: str = Field(min_length=3)
    role: str = "developer"
    # Grants composed IN THE INVITE — with no defaults (ADR-0009).
    grants: list[GrantSpec] = Field(default_factory=list)


class InviteSummary(BaseModel):
    id: str
    email: str
    role: str
    status: str
    expires_at: str | None = None


class InvitePreview(BaseModel):
    """What whoever OPENS the link sees.

    It does NOT carry the invitee's e-mail: whoever finds the link must not
    learn an address from it (ADR-0019).
    """

    id: str
    account_name: str
    role: str
    status: str
    expires_at: str | None = None
    usable: bool


class AcceptedInvite(BaseModel):
    """The account just joined, so the cockpit can switch to it with no second
    round trip: whoever accepts an invite wants to be inside."""

    account_id: str
    account_name: str
    role: str


class MemberRole(BaseModel):
    role: str


# ── use cases ───────────────────────────────────────────────────────────────


@log
async def me() -> MeResponse:
    """Who I am, in the active account.

    The `user_id` is NOT the identity provider's subject: it is the user's id in
    the core, resolved by EnsureUser during authentication.
    """
    ctx = auth_ctx.get()
    return MeResponse(
        subject=ctx.principal.subject,
        user_id=ctx.user_id,
        email=ctx.principal.email,
        name=ctx.principal.name,
        providers=ctx.principal.providers,
        account_id=ctx.account_id,
        role=ctx.role,
    )


@log
async def list_accounts() -> list[AccountSummary]:
    """The user's accounts — it feeds the cockpit's active-account selector.

    Without @account_scoped on purpose: it is precisely the call made BEFORE
    there is an active account.
    """
    ctx = auth_ctx.get()
    resp = await stubs.identity_stub().ListAccounts(
        identity_pb2.ListAccountsRequest(user=common_pb2.UserRef(id=ctx.user_id)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    # `items` carries the account AND the caller's role in it. Before it existed
    # this function could only report the role of the ACTIVE account and left
    # every other one empty, because inventing a role would have been lying
    # cheaply — the selector then could not tell an owner from a viewer until
    # after switching into each account.
    return [
        AccountSummary(
            id=it.account.id,
            handle=it.account.handle,
            display_name=it.account.display_name,
            kind=account_kind_name(it.account.kind),
            role=role_name(it.role),
        )
        for it in resp.items
    ]


@log
@account_scoped
async def create_account(body: NewAccount, idempotency_key: str = "") -> AccountSummary:
    """Creates an organization.

    Any user may create their own — hence no @require_role. What is required is
    an active account (SP-0): the creation happens from within a context.
    """
    account = await stubs.identity_stub().CreateAccount(
        identity_pb2.CreateAccountRequest(
            kind=identity_pb2.Account.KIND_ORGANIZATION,
            handle=body.handle,
            display_name=body.display_name,
            legal_id=body.legal_id,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return AccountSummary(
        id=account.id,
        handle=account.handle,
        display_name=account.display_name,
        kind=account_kind_name(account.kind),
        role="owner",  # whoever creates the organization owns it
    )


@log
@account_scoped
@require_role("owner", "admin")
async def list_members() -> list[MemberSummary]:
    """The active account's members — it requires a selected account AND a management role.

    The three stacked decorators are the pattern: log, scope, permission. The
    function's body does not know any of that exists — nor does the transport
    that called it.
    """
    ctx = auth_ctx.get()
    resp = await stubs.identity_stub().ListMemberships(
        identity_pb2.ListMembershipsRequest(account=common_pb2.AccountRef(id=ctx.account_id)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [
        MemberSummary(id=m.id, user_id=m.user.id, role=role_name(m.role)) for m in resp.memberships
    ]


@log
@account_scoped
@require_role("owner", "admin")
async def create_invite(body: NewInvite, idempotency_key: str = "") -> InviteSummary:
    """Invites somebody to the active account.

    The invite's token does NOT come back in the response: it goes out through
    the communication channel (P-11). Whoever loses the link needs a new invite.
    """
    invite = await stubs.identity_stub().CreateInvite(
        identity_pb2.CreateInviteRequest(
            email=body.email,
            role=role_value(body.role),
            grants=[
                identity_pb2.ResourceGrantSpec(
                    resource=common_pb2.ResourceRef(id=g.resource_id), level=g.level
                )
                for g in body.grants
            ],
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _invite(invite)


def _invite(invite) -> InviteSummary:
    return InviteSummary(
        id=invite.id,
        email=invite.email,
        role=role_name(invite.role),
        status=invite_status_name(invite.status),
        expires_at=_ts(invite.expires_at),
    )


def _ts(value) -> str | None:
    """A Timestamp that was never set comes back as None, not as 1970."""
    return value.ToDatetime().isoformat() + "Z" if value.seconds or value.nanos else None


@log
@account_scoped
@require_role("owner", "admin")
async def list_invites() -> list[InviteSummary]:
    """The active account's invites — the history, not only the pending ones."""
    resp = await stubs.identity_stub().ListInvites(
        identity_pb2.ListInvitesRequest(),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_invite(i) for i in resp.invites]


@log
async def get_invite(invite_id: str) -> InvitePreview:
    """The preview of whoever OPENS the link.

    It is deliberately NOT `@account_scoped`: whoever opens an invite may not be
    a member of anything yet, and requiring an active account here would be
    asking somebody to already be inside in order to be let in.
    """
    resp = await stubs.identity_stub().GetInvite(
        identity_pb2.GetInviteRequest(id=invite_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return InvitePreview(
        id=resp.id,
        account_name=resp.account_name,
        role=role_name(resp.role),
        status=invite_status_name(resp.status),
        expires_at=_ts(resp.expires_at),
        usable=resp.usable,
    )


@log
async def accept_invite(invite_id: str, idempotency_key: str = "") -> AcceptedInvite:
    """Accepts. The core requires the session's VERIFIED e-mail to be the
    invite's — the two refusals it can give are different walls, and the cockpit
    shows different texts for them (ADR-0019)."""
    ctx = auth_ctx.get()
    membership = await stubs.identity_stub().AcceptInvite(
        identity_pb2.AcceptInviteRequest(
            invite_id=invite_id,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    account = await stubs.identity_stub().GetAccount(
        identity_pb2.GetAccountRequest(id=membership.account.id),
        metadata=core.metadata_for(user_id=ctx.user_id, account_id=membership.account.id),
        timeout=_deadline(),
    )
    return AcceptedInvite(
        account_id=account.id,
        account_name=account.display_name,
        role=role_name(membership.role),
    )


@log
@account_scoped
@require_role("owner", "admin")
async def revoke_invite(invite_id: str) -> InviteSummary:
    """Revokes a pending invite."""
    invite = await stubs.identity_stub().RevokeInvite(
        identity_pb2.RevokeInviteRequest(id=invite_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _invite(invite)


@log
@account_scoped
@require_role("owner", "admin")
async def update_member(membership_id: str, body: MemberRole) -> MemberSummary:
    """Changes a member's role. The core keeps the invariant that the account is
    never left with no active owner."""
    m = await stubs.identity_stub().UpdateMembership(
        identity_pb2.UpdateMembershipRequest(
            membership_id=membership_id,
            role=role_value(body.role),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return MemberSummary(id=m.id, user_id=m.user.id, role=role_name(m.role))


async def remove_member(membership_id: str) -> bool:
    """Takes somebody out of the active account.

    The core sweeps the grants that person held HERE before removing the
    membership — a grant outliving the membership would be access with nothing
    to justify it. Their user, their personal account and what they created in
    this account are untouched (US-5.3).
    """
    await stubs.identity_stub().RemoveMembership(
        identity_pb2.RemoveMembershipRequest(membership_id=membership_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return True


_firebase_admin: FirebaseAdmin | None = None


def firebase_admin() -> FirebaseAdmin:
    """One instance per process, so the metadata token is cached rather than
    fetched on every sign-up."""
    global _firebase_admin
    if _firebase_admin is None:
        _firebase_admin = FirebaseAdmin(
            settings.firebase_project, settings.firebase_auth_emulator_host
        )
    return _firebase_admin


@log
async def send_email_verification() -> VerificationRequested:
    """Sends the message that proves the address of a password credential.

    The route is @token_only, not @account_scoped and not @public: the token
    says who is asking, and the core is NOT asked to resolve them — it would refuse with the
    very 412 this request exists to lift (spec SP-0 D-5, US-2).

    The address comes from the TOKEN, never from a body. Taking it from a body
    would let anybody with any valid token post a DOP-branded message to an
    address of their choosing, which is a phishing kit with our logo on it.
    """
    ctx = auth_ctx.get()
    email = (ctx.principal.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="this credential carries no e-mail")
    if ctx.principal.email_verified:
        # Not an error: asking twice is a person clicking twice, and a 4xx here
        # would show them a failure for something that is already true.
        return VerificationRequested(email=email)

    try:
        link = on_our_domain(
            await firebase_admin().verification_link(email), settings.firebase_auth_domain
        )
    except LinkNotGenerated as exc:
        logging.getLogger("dop-api").warning("verification link not generated: %s", exc)
        raise HTTPException(
            status_code=503, detail="could not generate the verification link"
        ) from exc

    await stubs.identity_stub().SendEmailVerification(
        identity_pb2.SendEmailVerificationRequest(
            email=email,
            subject=ctx.principal.subject,
            link=link,
            display_name=ctx.principal.name or "",
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return VerificationRequested(email=email)
