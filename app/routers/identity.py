"""Rotas de identidade — a fatia vertical que prova a stack de ponta a ponta.

Demonstra o padrão completo: decorators lendo o ContextVar, chamada ao core por
gRPC, e nenhuma linha de acesso a banco. Todo estado que aparece aqui veio de
uma resposta do dop-core (ADR-0016).
"""

from fastapi import APIRouter
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

router = APIRouter(prefix="/api/v1", tags=["identity"])


def _deadline() -> float:
    return settings.core_deadline_s


class MeResponse(BaseModel):
    subject: str
    user_id: str
    email: str
    name: str
    providers: list[str]
    account_id: str
    role: str


@router.get("/me", response_model=MeResponse)
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


class AccountSummary(BaseModel):
    id: str
    handle: str
    display_name: str
    kind: str
    role: str


@router.get("/accounts", response_model=list[AccountSummary])
@log
async def list_accounts() -> list[AccountSummary]:
    """Contas do usuário — alimenta o seletor de conta ativa do cockpit.

    Sem @account_scoped de propósito: é justamente a chamada que o cockpit faz
    ANTES de haver conta ativa.
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


class NewAccount(BaseModel):
    handle: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    legal_id: str = ""  # CNPJ


@router.post("/accounts", response_model=AccountSummary, status_code=201)
@log
@account_scoped
async def create_account(body: NewAccount) -> AccountSummary:
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
            idempotency_key=core.idempotency_key(),
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


class MemberSummary(BaseModel):
    id: str
    user_id: str
    role: str


@router.get("/accounts/current/members", response_model=list[MemberSummary])
@log
@account_scoped
@require_role("owner", "admin")
async def list_members() -> list[MemberSummary]:
    """Membros da conta ativa — exige conta selecionada E papel de gestão.

    Os três decorators empilhados são o padrão: log, escopo, permissão. O corpo
    da função não sabe que nada disso existe.
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


@router.post("/invites", response_model=InviteSummary, status_code=201)
@log
@account_scoped
@require_role("owner", "admin")
async def create_invite(body: NewInvite) -> InviteSummary:
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
            idempotency_key=core.idempotency_key(),
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
