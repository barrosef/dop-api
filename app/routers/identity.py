"""Rotas de identidade — a fatia vertical que prova a stack de ponta a ponta.

Demonstra o padrão completo: decorators lendo o ContextVar, chamada ao core por
gRPC, e nenhuma linha de acesso a banco.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role

router = APIRouter(prefix="/api/v1", tags=["identity"])


class MeResponse(BaseModel):
    subject: str
    email: str
    name: str
    providers: list[str]
    account_id: str
    role: str


@router.get("/me", response_model=MeResponse)
@log
async def me() -> MeResponse:
    """Quem sou eu, na conta ativa."""
    ctx = auth_ctx.get()
    return MeResponse(
        subject=ctx.principal.subject,
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
    """Contas do usuário — alimenta o seletor de conta ativa do cockpit."""
    # identity_pb2_grpc.IdentityServiceStub(core.channel).ListAccounts(...)
    return []


@router.get("/accounts/current/members")
@log
@account_scoped
@require_role("owner", "admin")
async def list_members() -> list[dict]:
    """Membros da conta ativa — exige conta selecionada E papel de gestão.

    Os três decorators empilhados são o padrão: log, escopo, permissão. O corpo
    da função não sabe que nada disso existe.
    """
    return []
