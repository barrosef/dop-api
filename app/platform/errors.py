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


# ── o mesmo mapeamento, na direção do transporte gRPC ────────────────────────
# A porta gRPC do BFF precisa devolver STATUS gRPC, não código HTTP. Em vez de
# um segundo dicionário (que envelheceria em separado), reaproveitamos os dois
# que já existem: `http_status_for` classifica, `_detail_for` redige. A regra de
# não vazar detalhe de 5xx do núcleo passa a valer nos dois transportes por
# construção, e não por disciplina.

_HTTP_TO_STATUS: dict[int, StatusCode] = {
    400: StatusCode.INVALID_ARGUMENT,
    401: StatusCode.UNAUTHENTICATED,
    403: StatusCode.PERMISSION_DENIED,
    404: StatusCode.NOT_FOUND,
    409: StatusCode.ALREADY_EXISTS,
    412: StatusCode.FAILED_PRECONDITION,
    429: StatusCode.RESOURCE_EXHAUSTED,
    501: StatusCode.UNIMPLEMENTED,
    503: StatusCode.UNAVAILABLE,
    504: StatusCode.DEADLINE_EXCEEDED,
}


def grpc_status_for_http(status: int) -> StatusCode:
    """HTTP → gRPC, para o erro que os decorators levantam como HTTPException.

    Os decorators de segurança falam HTTPException porque nasceram no REST; em
    vez de reescrevê-los (e arriscar o comportamento HTTP já testado), a porta
    gRPC traduz na saída. 5xx desconhecido vira INTERNAL.
    """
    return _HTTP_TO_STATUS.get(status, StatusCode.INTERNAL)


def as_grpc(exc: AioRpcError) -> tuple[StatusCode, str]:
    """Erro do núcleo → (status, detalhe) para devolver ao cliente gRPC.

    O status do núcleo ATRAVESSA intacto — NOT_FOUND continua NOT_FOUND, sem
    passar por HTTP e voltar. O que muda é só o detalhe, redigido pelo mesmo
    `_detail_for` do REST: mensagem de 5xx pode carregar host, query ou
    credencial e não sai daqui.
    """
    return exc.code(), _detail_for(exc, http_status_for(exc.code()))


def as_grpc_from_http(exc: HTTPException) -> tuple[StatusCode, str]:
    """HTTPException dos decorators → (status, detalhe) para o cliente gRPC.

    `as_http` já redigiu o que veio do núcleo; o que sobra aqui são as recusas
    da própria borda ("Nenhuma conta ativa selecionada", "Sem vínculo com a
    conta"), que são para o cliente ler mesmo.
    """
    status = grpc_status_for_http(exc.status_code)
    detail = "erro interno" if exc.status_code >= 500 else str(exc.detail)
    return status, detail
