"""gRPC status → HTTP translation.

The BFF does not invent error semantics: it translates the core's. An error the
core classified as NOT_FOUND becomes a 404 here, with no interpretation in
between.
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
    # A 5xx does not expose an internal detail to the client: the core's error
    # message may carry a host, a query or a credential. The rule holds on BOTH
    # paths — the route handler and the translation inside the middleware.
    return "internal error" if status >= 500 else (exc.details() or "core error")


async def grpc_exception_handler(_request: Request, exc: AioRpcError) -> JSONResponse:
    status = http_status_for(exc.code())
    return JSONResponse({"detail": _detail_for(exc, status)}, status_code=status)


def as_http(exc: AioRpcError) -> HTTPException:
    """The same translation, for whoever cannot rely on the application handler.

    AuthMiddleware runs ABOVE Starlette's ExceptionMiddleware: an AioRpcError
    raised there would not pass through `grpc_exception_handler`.
    """
    status = http_status_for(exc.code())
    return HTTPException(status_code=status, detail=_detail_for(exc, status))


# ── the same mapping, in the gRPC transport's direction ─────────────────────
# The BFF's gRPC port has to return a gRPC STATUS, not an HTTP code. Rather than
# a second dictionary (which would age separately), we reuse the two that
# already exist: `http_status_for` classifies, `_detail_for` writes. The rule of
# not leaking a 5xx detail from the core comes to hold on both transports by
# construction, and not by discipline.

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
    """HTTP → gRPC, for the error the decorators raise as an HTTPException.

    The security decorators speak HTTPException because they were born in REST;
    rather than rewriting them (and risking the already tested HTTP behaviour),
    the gRPC port translates on the way out. An unknown 5xx becomes INTERNAL.
    """
    return _HTTP_TO_STATUS.get(status, StatusCode.INTERNAL)


def as_grpc(exc: AioRpcError) -> tuple[StatusCode, str]:
    """A core error → (status, detail) to return to the gRPC client.

    The core's status CROSSES intact — NOT_FOUND stays NOT_FOUND, without going
    through HTTP and back. What changes is only the detail, written by the same
    `_detail_for` as REST: a 5xx message may carry a host, a query or a
    credential and does not leave here.
    """
    return exc.code(), _detail_for(exc, http_status_for(exc.code()))


def as_grpc_from_http(exc: HTTPException) -> tuple[StatusCode, str]:
    """The decorators' HTTPException → (status, detail) for the gRPC client.

    `as_http` has already written what came from the core; what remains here are
    the edge's own refusals ("No active account selected", "No membership in the
    account"), which are meant for the client to read.
    """
    status = grpc_status_for_http(exc.status_code)
    detail = "internal error" if exc.status_code >= 500 else str(exc.detail)
    return status, detail
