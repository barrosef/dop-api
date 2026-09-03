"""The authentication middleware: it verifies the token and resolves the active account."""

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.platform.context import AuthContext, auth_ctx
from app.platform.security.decorator import is_public
from app.platform.security.firebase import FirebaseVerifier, InvalidToken


class AuthMiddleware(BaseHTTPMiddleware):
    """It fills in auth_ctx. The decorators read from there; handlers see nothing."""

    def __init__(self, app, verifier: FirebaseVerifier, resolver=None):
        super().__init__(app)
        self.verifier = verifier
        # resolver(principal, account_id) -> (user_id, role, grants)
        # Implemented by the coreclient: the one that resolves the role and the
        # grants is the CORE.
        self.resolver = resolver

    async def dispatch(self, request: Request, call_next):
        if is_public(request) or request.url.path in ("/healthz", "/docs", "/openapi.json"):
            return await call_next(request)

        raw = request.headers.get("authorization", "")
        if not raw:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        try:
            principal = await self.verifier.verify(raw)
        except InvalidToken as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)

        # The active account comes from the header — it is the interface's selector.
        account_id = request.headers.get("x-account-id", "")

        user_id, role, grants = "", "", {}
        if self.resolver is not None:
            try:
                user_id, role, grants = await self.resolver(principal, account_id)
            except HTTPException as exc:
                # Middleware runs ABOVE Starlette's ExceptionMiddleware: an
                # HTTPException raised here would be translated by nobody and
                # would become a 500. So we translate it by hand — it is how "no
                # membership in the account" reaches the client as a 403.
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            except Exception:
                return JSONResponse({"detail": "Failed to resolve the account"}, status_code=503)

        token = auth_ctx.set(
            AuthContext(
                principal=principal,
                raw_token=raw.removeprefix("Bearer ").strip(),
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
