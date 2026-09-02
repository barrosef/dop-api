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
