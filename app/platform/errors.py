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


async def grpc_exception_handler(_request: Request, exc: AioRpcError) -> JSONResponse:
    status = http_status_for(exc.code())
    detail = exc.details() or "erro no núcleo"
    # 5xx não expõe detalhe interno ao cliente.
    if status >= 500:
        detail = "erro interno"
    return JSONResponse({"detail": detail}, status_code=status)


def as_http(exc: AioRpcError) -> HTTPException:
    status = http_status_for(exc.code())
    return HTTPException(status_code=status, detail=exc.details() or "erro no núcleo")
