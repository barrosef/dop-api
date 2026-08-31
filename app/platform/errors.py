"""Tradução status gRPC → HTTP.

O BFF não inventa semântica de erro: ele traduz a do core. Erro que o core
classificou como NOT_FOUND vira 404 aqui, sem interpretação no meio.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from grpc import StatusCode
from grpc.aio import AioRpcError

_STATUS_TO_HTTP: dict[StatusCode, int] = {
    StatusCode.OK: 200,
    StatusCode.INVALID_ARGUMENT: 400,
    StatusCode.UNAUTHENTICATED: 401,
    StatusCode.PERMISSION_DENIED: 403,
    StatusCode.NOT_FOUND: 404,
    StatusCode.ALREADY_EXISTS: 409,
    StatusCode.ABORTED: 409,
    StatusCode.FAILED_PRECONDITION: 412,
    StatusCode.RESOURCE_EXHAUSTED: 429,
    StatusCode.UNIMPLEMENTED: 501,
    StatusCode.UNAVAILABLE: 503,
    StatusCode.DEADLINE_EXCEEDED: 504,
}


def http_status_for(code: StatusCode) -> int:
    return _STATUS_TO_HTTP.get(code, 500)


def _detail_for(exc: AioRpcError, status: int) -> str:
    # 5xx não expõe detalhe interno ao cliente: mensagem de erro do núcleo pode
    # carregar host, query ou credencial. A regra vale nos DOIS caminhos —
    # handler de rota e tradução dentro do middleware.
    return "erro interno" if status >= 500 else (exc.details() or "erro no núcleo")


async def grpc_exception_handler(_request: Request, exc: AioRpcError) -> JSONResponse:
    status = http_status_for(exc.code())
    return JSONResponse({"detail": _detail_for(exc, status)}, status_code=status)


def as_http(exc: AioRpcError) -> HTTPException:
    """Mesma tradução, para quem não pode contar com o handler da aplicação.

    O AuthMiddleware roda ACIMA do ExceptionMiddleware do Starlette: um
    AioRpcError levantado lá não passaria pelo `grpc_exception_handler`.
    """
    status = http_status_for(exc.code())
    return HTTPException(status_code=status, detail=_detail_for(exc, status))
