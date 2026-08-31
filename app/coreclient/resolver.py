"""Resolver de autorização — quem o usuário é e o que ele pode, segundo o CORE.

Roda uma vez por requisição, dentro do AuthMiddleware, entre a verificação do
token e a execução do handler. Devolve a tripla `(user_id, role, grants)` que
compõe o AuthContext.

A regra que este módulo defende: o BFF NÃO decide permissão. Ele pergunta.
Papel e concessões vivem no núcleo, junto do estado que os justifica
(ADR-0016). Se um dia aparecer aqui uma tabela de papéis, a fronteira caiu.
"""

from fastapi import HTTPException
from grpc import StatusCode
from grpc.aio import AioRpcError

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context, role_name
from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.platform.context import Principal
from app.platform.errors import as_http
from app.platform.logging.config import get_logger
from app.settings import settings

# Falhas que NÃO devem derrubar a requisição ao descobrir o papel: o vínculo já
# foi provado por ListAccounts. Serviço ainda não registrado no core, ou papel
# sem permissão de listar membros, viram papel vazio — o @require_role recusa
# depois, com 403, que é a resposta correta.
_TOLERAVEIS = (
    StatusCode.UNIMPLEMENTED,
    StatusCode.PERMISSION_DENIED,
    StatusCode.NOT_FOUND,
)


class CoreResolver:
    """`resolver(principal, account_id) -> (user_id, role, grants)`."""

    def __init__(self, deadline_s: float | None = None):
        self._deadline = deadline_s if deadline_s is not None else settings.core_deadline_s

    async def __call__(
        self, principal: Principal, account_id: str
    ) -> tuple[str, str, dict[str, str]]:
        actor_name = principal.name or principal.email
        user_id = await self._ensure_user(principal)

        if not account_id:
            # Sem conta ativa ainda é um estado VÁLIDO: é assim que o cockpit
            # carrega o seletor de contas logo após o login. Quem exige conta é
            # o @account_scoped, na rota.
            return user_id, "", {}

        md = core.metadata_for(user_id=user_id, account_id=account_id, actor_name=actor_name)
        ctx = call_context(user_id=user_id, account_id=account_id, actor_name=actor_name)

        await self._assert_membership(user_id, account_id, ctx, md)
        role = await self._role_in_account(user_id, ctx, md)
        return user_id, role, self._grants()

    async def _ensure_user(self, principal: Principal) -> str:
        """Idempotente por desenho — roda em TODO login, não só no primeiro.

        É o que garante que o usuário do provedor de identidade exista no core
        antes de qualquer outra pergunta. Não leva CallContext: acontece antes
        de existir conta ativa.
        """
        req = identity_pb2.EnsureUserRequest(
            subject=principal.subject,
            email=principal.email,
            email_verified=principal.email_verified,
            name=principal.name,
            avatar_url=principal.avatar_url,
            provider=principal.providers[0] if principal.providers else "",
            idempotency_key=core.idempotency_key(),
        )
        try:
            user = await stubs.identity_stub().EnsureUser(
                req, metadata=core.metadata_for(), timeout=self._deadline
            )
        except AioRpcError as exc:
            raise as_http(exc) from exc
        return user.id

    async def _assert_membership(self, user_id, account_id, ctx, md) -> None:
        """Sem vínculo com a conta pedida, a requisição morre aqui com 403."""
        req = identity_pb2.ListAccountsRequest(ctx=ctx, user=common_pb2.UserRef(id=user_id))
        try:
            resp = await stubs.identity_stub().ListAccounts(
                req, metadata=md, timeout=self._deadline
            )
        except AioRpcError as exc:
            raise as_http(exc) from exc

        if account_id not in {a.id for a in resp.accounts}:
            raise HTTPException(status_code=403, detail="Sem vínculo com a conta solicitada")

    async def _role_in_account(self, user_id, ctx, md) -> str:
        req = identity_pb2.ListMembershipsRequest(
            ctx=ctx, account=common_pb2.AccountRef(id=ctx.account.id)
        )
        try:
            resp = await stubs.identity_stub().ListMemberships(
                req, metadata=md, timeout=self._deadline
            )
        except AioRpcError as exc:
            if exc.code() in _TOLERAVEIS:
                get_logger().warning(
                    "papel não resolvido", account_id=ctx.account.id, code=str(exc.code())
                )
                return ""
            raise as_http(exc) from exc

        for m in resp.memberships:
            if m.user.id == user_id:
                return role_name(m.role)
        return ""

    @staticmethod
    def _grants() -> dict[str, str]:
        """Concessões de recurso — hoje sempre vazias, e isso está correto.

        `resource.proto` expõe GrantResource/RevokeGrant, mas ainda NÃO uma
        consulta de concessões por usuário; e o ResourceService sequer está
        registrado no core (internal/app/register.go). Devolver vazio degrada
        com elegância: owner e admin continuam com `manage` implícito
        (AuthContext.grant_level), e developer/viewer recebem 403 do
        @require_grant — que é o comportamento seguro. Quando o RPC existir, é
        este método, e só ele, que muda.
        """
        return {}
