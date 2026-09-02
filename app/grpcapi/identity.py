"""The identity gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/identity.py`: it receives a message, calls the SAME
function from `app/usecases/identity.py`, returns another message. No decision
happens in this file — neither authorization nor a call to the core. If an
`if ctx.role ==` shows up here, the duplication has begun.

Authentication and the active account appear in no RPC's signature: the
interceptor has already resolved the principal from the token and filled in the
same ContextVar REST uses. The decorators in the use case read from there.
"""

from app.grpcapi import convert
from app.grpcapi.gen.dop.bff.v1 import identity_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import identity_pb2_grpc as bff_grpc
from app.usecases import identity as uc


def _ts(value: str | None):
    """An absent date stays ABSENT on the wire — a zeroed one would say 1970."""
    if not value:
        return None
    from datetime import datetime

    from google.protobuf.timestamp_pb2 import Timestamp

    ts = Timestamp()
    ts.FromDatetime(datetime.fromisoformat(value.replace("Z", "")))
    return ts


def _invite(i: uc.InviteSummary) -> bff.InviteSummary:
    msg = bff.InviteSummary(
        id=i.id,
        email=i.email,
        role=convert.role_enum(i.role),
        status=convert.status_enum(i.status),
    )
    if ts := _ts(i.expires_at):
        msg.expires_at.CopyFrom(ts)
    return msg


def _account(a: uc.AccountSummary) -> bff.AccountSummary:
    return bff.AccountSummary(
        id=a.id,
        kind=convert.kind_enum(a.kind),
        handle=a.handle,
        display_name=a.display_name,
        role=convert.role_enum(a.role),
    )


class IdentityServicer(bff_grpc.IdentityServiceServicer):
    async def GetMe(self, request: bff.GetMeRequest, context) -> bff.Me:
        r = await uc.me()
        return bff.Me(
            subject=r.subject,
            user_id=r.user_id,
            email=r.email,
            name=r.name,
            providers=r.providers,
            account_id=r.account_id,
            role=convert.role_enum(r.role),
        )

    async def ListAccounts(
        self, request: bff.ListAccountsRequest, context
    ) -> bff.ListAccountsResponse:
        return bff.ListAccountsResponse(accounts=[_account(a) for a in await uc.list_accounts()])

    async def ListMembers(
        self, request: bff.ListMembersRequest, context
    ) -> bff.ListMembersResponse:
        membros = await uc.list_members()
        return bff.ListMembersResponse(
            members=[
                bff.MemberSummary(id=m.id, user_id=m.user_id, role=convert.role_enum(m.role))
                for m in membros
            ]
        )

    async def ListInvites(
        self, request: bff.ListInvitesRequest, context
    ) -> bff.ListInvitesResponse:
        return bff.ListInvitesResponse(invites=[_invite(i) for i in await uc.list_invites()])

    async def GetInvite(self, request: bff.GetInviteRequest, context) -> bff.InvitePreview:
        p = await uc.get_invite(request.id)
        msg = bff.InvitePreview(
            id=p.id,
            account_name=p.account_name,
            role=convert.role_enum(p.role),
            status=convert.status_enum(p.status),
            usable=p.usable,
        )
        if ts := _ts(p.expires_at):
            msg.expires_at.CopyFrom(ts)
        return msg

    async def AcceptInvite(
        self, request: bff.AcceptInviteRequest, context
    ) -> bff.AcceptInviteResponse:
        r = await uc.accept_invite(request.id)
        return bff.AcceptInviteResponse(
            account=bff.AccountSummary(id=r.account_id, display_name=r.account_name),
            role=convert.role_enum(r.role),
        )

    async def RevokeInvite(self, request: bff.RevokeInviteRequest, context) -> bff.InviteSummary:
        return _invite(await uc.revoke_invite(request.id))

    async def UpdateMember(self, request: bff.UpdateMemberRequest, context) -> bff.MemberSummary:
        m = await uc.update_member(
            request.membership_id, uc.MemberRole(role=convert.role_name(request.role))
        )
        return bff.MemberSummary(id=m.id, user_id=m.user_id, role=convert.role_enum(m.role))

    async def CreateAccount(
        self, request: bff.CreateAccountRequest, context
    ) -> bff.AccountSummary:
        return _account(
            await uc.create_account(
                uc.NewAccount(
                    handle=request.handle,
                    display_name=request.display_name,
                    legal_id=request.legal_id,
                ),
                # The client's key wins; empty, the use case generates its own.
                idempotency_key=request.idempotency_key,
            )
        )

    async def CreateInvite(self, request: bff.CreateInviteRequest, context) -> bff.InviteSummary:
        # An absent role (ROLE_UNSPECIFIED) is OMITTED rather than becoming an
        # empty string: that way the one that chooses the default is the use
        # case's model, the same one REST uses. Translating it to "" here would
        # give an invite with no role in gRPC and with "developer" in REST — a
        # silent divergence.
        campos: dict = {
            "email": request.email,
            "grants": [
                uc.GrantSpec(resource_id=g.resource_id, level=g.level) for g in request.grants
            ],
        }
        if nome := convert.role_name(request.role):
            campos["role"] = nome

        r = await uc.create_invite(
            uc.NewInvite(**campos),
            idempotency_key=request.idempotency_key,
        )
        return bff.InviteSummary(
            id=r.id,
            email=r.email,
            role=convert.role_enum(r.role),
            status=convert.status_enum(r.status),
        )
