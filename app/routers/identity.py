"""Rotas de identidade — adaptador HTTP sobre os casos de uso.

O que este módulo faz: receber HTTP, chamar `app/usecases/identity.py`,
devolver JSON. O que ele NÃO faz: decidir nada. A regra, a autorização e a
conversa com o núcleo vivem no caso de uso, que a porta gRPC chama igualzinho
(`app/grpcapi/identity.py`) — é assim que os dois transportes não podem
divergir.

Os decorators (@log, @account_scoped, @require_role) também estão lá, não aqui:
autorização presa ao router valeria só para o REST.
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

# Reexportados para quem já importava os modelos daqui — eles são os DTOs da
# borda, compartilhados com o gRPC, e agora moram no caso de uso.
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
    """Quem sou eu, na conta ativa."""
    return await uc.me()


@router.get("/accounts", response_model=list[AccountSummary])
async def list_accounts() -> list[AccountSummary]:
    """Contas do usuário — alimenta o seletor de conta ativa do cockpit."""
    return await uc.list_accounts()


@router.post("/accounts", response_model=AccountSummary, status_code=201)
async def create_account(body: NewAccount) -> AccountSummary:
    """Cria uma organização.

    O REST não tem onde o cliente carregar a chave de idempotência, então o
    caso de uso gera uma. No gRPC o cliente pode mandar a sua.
    """
    return await uc.create_account(body)


@router.get("/accounts/current/members", response_model=list[MemberSummary])
async def list_members() -> list[MemberSummary]:
    """Membros da conta ativa — exige conta selecionada E papel de gestão."""
    return await uc.list_members()


@router.post("/invites", response_model=InviteSummary, status_code=201)
async def create_invite(body: NewInvite) -> InviteSummary:
    """Convida alguém para a conta ativa."""
    return await uc.create_invite(body)
