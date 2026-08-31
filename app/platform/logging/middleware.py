"""Middleware que abre o contexto de log de cada requisição."""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.platform.context import request_ctx
from app.platform.logging.config import FIELD_DURATION_MS, get_logger


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Aceita o request_id de quem chamou (rastro entre serviços) ou cria um.
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_ctx.set(
            {
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "client_ip": request.client.host if request.client else "",
            }
        )
        start = time.perf_counter()
        logger = get_logger()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "request falhou",
                path=request.url.path,
                method=request.method,
                error=str(exc),
                **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
            )
            request_ctx.reset(token)
            raise
        response.headers["x-request-id"] = request_id
        logger.info(
            "request",
            path=request.url.path,
            method=request.method,
            status=response.status_code,
            **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
        )
        request_ctx.reset(token)
        return response
