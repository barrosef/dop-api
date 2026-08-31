"""Servicer gRPC de identidade — adaptador protobuf sobre os casos de uso.

Simétrico ao `app/routers/identity.py`: recebe uma mensagem, chama a MESMA
função de `app/usecases/identity.py`, devolve outra mensagem. Nenhuma decisão
acontece neste arquivo — nem autorização, nem chamada ao núcleo. Se aparecer
aqui um `if ctx.role ==`, a duplicação começou.

Autenticação e conta ativa não aparecem na assinatura de nenhum RPC: o
interceptor já resolveu o principal a partir do token e preencheu o mesmo
ContextVar que o REST usa. Os decorators no caso de uso leem dali.
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
                # A chave do cliente vence; vazia, o caso de uso gera a sua.
                idempotency_key=request.idempotency_key,
            )
        )

    async def CreateInvite(self, request: bff.CreateInviteRequest, context) -> bff.InviteSummary:
        # Papel ausente (ROLE_UNSPECIFIED) é OMITIDO em vez de virar string
        # vazia: assim quem escolhe o padrão é o modelo do caso de uso, o mesmo
        # que o REST usa. Traduzir para "" aqui daria um convite sem papel no
        # gRPC e com "developer" no REST — divergência silenciosa.
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
