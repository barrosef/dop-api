"""Middleware de autenticação: verifica o token e resolve a conta ativa."""

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.platform.context import AuthContext, auth_ctx
from app.platform.security.decorator import is_public
from app.platform.security.firebase import FirebaseVerifier, InvalidToken


class AuthMiddleware(BaseHTTPMiddleware):
    """Preenche o auth_ctx. Os decorators leem dali; handlers não veem nada."""

    def __init__(self, app, verifier: FirebaseVerifier, resolver=None):
        super().__init__(app)
        self.verifier = verifier
        # resolver(principal, account_id) -> (user_id, role, grants)
        # Implementado pelo coreclient: quem resolve papel e concessões é o CORE.
        self.resolver = resolver

    async def dispatch(self, request: Request, call_next):
        if is_public(request) or request.url.path in ("/healthz", "/docs", "/openapi.json"):
            return await call_next(request)

        raw = request.headers.get("authorization", "")
        if not raw:
            return JSONResponse({"detail": "Não autenticado"}, status_code=401)

        try:
            principal = await self.verifier.verify(raw)
        except InvalidToken as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)

        # Conta ativa vem do cabeçalho — é o seletor da interface.
        account_id = request.headers.get("x-account-id", "")

        user_id, role, grants = "", "", {}
        if self.resolver is not None:
            try:
                user_id, role, grants = await self.resolver(principal, account_id)
            except HTTPException:
                raise
            except Exception:
                return JSONResponse({"detail": "Falha ao resolver a conta"}, status_code=503)

        token = auth_ctx.set(
            AuthContext(
                principal=principal,
                user_id=user_id,
                account_id=account_id,
                role=role,
                grants=grants,
            )
        )
        try:
            return await call_next(request)
        finally:
            auth_ctx.reset(token)
