"""Casos de uso de identidade — chamados por REST e por gRPC, sem duplicação.

Estas funções são o ÚNICO lugar onde a regra vive. `app/routers/identity.py`
traduz HTTP para elas; `app/grpcapi/identity.py` traduz protobuf para as mesmas
funções. Nenhuma das duas pontas repete uma linha de decisão.

Os transversais também moram aqui, não nos adaptadores: `@log`, `@account_scoped`
e `@require_role` decoram o CASO DE USO, e por isso valem igualmente nos dois
transportes. Se a autorização ficasse no router, a porta gRPC nasceria aberta.

Os modelos de resposta são Pydantic puro — nada de FastAPI — justamente para
que o servicer gRPC possa consumi-los sem arrastar o framework web junto.
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import (
    account_kind_name,
    call_context_from,
    invite_status_name,
    role_name,
    role_value,
)
from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    """Chave de idempotência do cliente, ou uma nossa (ADR-0017).

    O contrato gRPC deixa o cliente enviar a dele — é ele quem sabe se está
    retentando ou pedindo outra coisa. O REST não tem onde carregá-la, então
    geramos: protege contra o retry do canal, que já é o caso comum.
    """
    return chave or core.idempotency_key()


# ── modelos da borda ────────────────────────────────────────────────────────


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


class NewAccount(BaseModel):
    handle: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    legal_id: str = ""  # CNPJ


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
    # Concessões compostas NO CONVITE — sem defaults (ADR-0013).
    grants: list[GrantSpec] = Field(default_factory=list)


class InviteSummary(BaseModel):
    id: str
    email: str
    role: str
    status: str


# ── casos de uso ────────────────────────────────────────────────────────────


@log
async def me() -> MeResponse:
    """Quem sou eu, na conta ativa.

    O `user_id` NÃO é o subject do provedor de identidade: é o id do usuário no
    core, resolvido pelo EnsureUser durante a autenticação.
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
    """Contas do usuário — alimenta o seletor de conta ativa do cockpit.

    Sem @account_scoped de propósito: é justamente a chamada que se faz ANTES
    de haver conta ativa.
    """
    ctx = auth_ctx.get()
    resp = await stubs.identity_stub().ListAccounts(
        identity_pb2.ListAccountsRequest(
            ctx=call_context_from(ctx), user=common_pb2.UserRef(id=ctx.user_id)
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [
        AccountSummary(
            id=a.id,
            handle=a.handle,
            display_name=a.display_name,
            kind=account_kind_name(a.kind),
            # O papel só é conhecido para a conta ATIVA — foi ela que o resolver
            # consultou. Inventar papel para as outras seria mentir barato.
            role=ctx.role if a.id == ctx.account_id else "",
        )
        for a in resp.accounts
    ]


@log
@account_scoped
async def create_account(body: NewAccount, idempotency_key: str = "") -> AccountSummary:
    """Cria uma organização.

    Qualquer usuário pode criar a sua — por isso não há @require_role. O que se
    exige é conta ativa (SP-0): a criação acontece a partir de um contexto.
    """
    ctx = auth_ctx.get()
    account = await stubs.identity_stub().CreateAccount(
        identity_pb2.CreateAccountRequest(
            ctx=call_context_from(ctx),
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
        role="owner",  # quem cria a organização é o dono dela
    )


@log
@account_scoped
@require_role("owner", "admin")
async def list_members() -> list[MemberSummary]:
    """Membros da conta ativa — exige conta selecionada E papel de gestão.

    Os três decorators empilhados são o padrão: log, escopo, permissão. O corpo
    da função não sabe que nada disso existe — e nem o transporte que a chamou.
    """
    ctx = auth_ctx.get()
    resp = await stubs.identity_stub().ListMemberships(
        identity_pb2.ListMembershipsRequest(
            ctx=call_context_from(ctx), account=common_pb2.AccountRef(id=ctx.account_id)
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [
        MemberSummary(id=m.id, user_id=m.user.id, role=role_name(m.role))
        for m in resp.memberships
    ]


@log
@account_scoped
@require_role("owner", "admin")
async def create_invite(body: NewInvite, idempotency_key: str = "") -> InviteSummary:
    """Convida alguém para a conta ativa.

    O token do convite NÃO volta na resposta: ele sai pelo canal de comunicação
    (P-11). Quem perder o link precisa de convite novo.
    """
    ctx = auth_ctx.get()
    invite = await stubs.identity_stub().CreateInvite(
        identity_pb2.CreateInviteRequest(
            ctx=call_context_from(ctx),
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
    return InviteSummary(
        id=invite.id,
        email=invite.email,
        role=role_name(invite.role),
        status=invite_status_name(invite.status),
    )
