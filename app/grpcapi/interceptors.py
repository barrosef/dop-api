"""The gRPC server's interceptors — the SAME cross-cutting concerns as REST, another transport.

Each mirrors a middleware that already exists:

    LoggingInterceptor  ↔  app/platform/logging/middleware.LoggingMiddleware
    AuthInterceptor     ↔  app/platform/security/middleware.AuthMiddleware
    ErrorInterceptor    ↔  app/platform/errors.grpc_exception_handler

The order in which they enter the server is the HTTP stack's: logging outermost
(it opens the context and measures everything, the authentication failure
included), errors in the middle, auth innermost. What changes is only the
mechanics.

    A trap already solved: in `grpc.aio`, a ContextVar set inside
    `intercept_service` does NOT reach the handler — the interceptor only
    returns the handler, which the library runs later. That is why every
    interceptor here WRAPS the behaviour (`handler.unary_unary`) instead of
    simply preparing the ground before calling the continuation. It is that
    wrapper that makes `auth_ctx` and `request_ctx` hold inside the servicer, as
    in REST.

The STREAMING wrapper (`handler.unary_stream`) has two extra requirements, and
getting either wrong makes the RPC fail in a way that is hard to trace back:

1. **It has to be a GENERATOR function.** `grpc.aio` decides how to run the
   handler by looking at it: a true `iscoroutinefunction` means the
   reader/writer style (`await context.write(...)`, returns None); false means an
   asynchronous generator. A wrapper written as an `async def` with no `yield`
   would be classified as the first style, and the server would never send the
   items the servicer produced — with no error at all, just an empty stream.
   That is why every `_wrap_stream` here contains a `yield`.

2. **The ContextVar lives THROUGHOUT the whole stream.** In a unary call,
   `set`/`reset` bracket an `await`. Here they bracket an `async for` that lasts
   as long as the client stays connected — and the `reset` happens when the
   generator is finalized, which may be done by the asynchronous generator
   collector, in another context. Hence the `reset` being tolerant of failure:
   the RPC task's context dies with it anyway, and bringing the shutdown down
   over that would trade a detail of hygiene for a visible error.
"""

import contextlib
import time
import uuid
from collections.abc import Callable

import grpc
from fastapi import HTTPException
from grpc.aio import AioRpcError
from pydantic import ValidationError

from app.platform.context import AuthContext, auth_ctx, request_ctx
from app.platform.errors import as_grpc, as_grpc_from_http
from app.platform.logging.config import FIELD_DURATION_MS, get_logger
from app.platform.security.firebase import FirebaseVerifier, InvalidToken


class _Interceptor(grpc.aio.ServerInterceptor):
    """The base: it wraps unary AND server-streaming, each with its own wrapper.

    The two cases need DIFFERENT wrappers (the streaming one is a generator
    function, see the module's docstring), but the decision of which to use is
    always the same — so it lives here, once, and not in each interceptor.

    `stream_unary` and `stream_stream` pass through intact: the edge's contract
    exposes neither, and a wrapper written blind for them would be unexercised
    code pretending to be coverage.
    """

    def _wrap(self, inner: Callable, details) -> Callable:  # pragma: no cover - abstract
        raise NotImplementedError

    def _wrap_stream(self, inner: Callable, details) -> Callable:  # pragma: no cover
        raise NotImplementedError

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None:
            return handler
        if handler.unary_unary is not None:
            return grpc.unary_unary_rpc_method_handler(
                self._wrap(handler.unary_unary, handler_call_details),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        if handler.unary_stream is not None:
            return grpc.unary_stream_rpc_method_handler(
                self._wrap_stream(handler.unary_stream, handler_call_details),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        return handler


def _reset(var, token) -> None:
    """Returns the ContextVar to its previous value, tolerating somebody else's finalization.

    See item 2 of the module's docstring: an asynchronous generator's `finally`
    may run in a context different from the one that created the token, and then
    the `reset` raises a ValueError. That is hygiene, not correctness — the RPC's
    task (and its context) ends along with it.
    """
    with contextlib.suppress(ValueError):
        var.reset(token)


def _metadata(context) -> dict[str, str]:
    return {k: v for k, v in (context.invocation_metadata() or ())}


class LoggingInterceptor(_Interceptor):
    """Opens the call's logging context — canonical fields identical to REST's.

    `path` is the full gRPC method and `method` is "grpc": it is what allows
    filtering by transport in an aggregated log where both ports write to the
    same place. The secret mask does not have to be repeated here — it is a
    structlog processor, so it already covers everything that goes out through
    `get_logger()`.
    """

    def _wrap(self, inner, details):
        async def behavior(request, context):
            md = _metadata(context)
            # It accepts the caller's request_id (a trace between services) or
            # creates one.
            request_id = md.get("x-request-id") or uuid.uuid4().hex
            token = request_ctx.set(
                {
                    "request_id": request_id,
                    "path": details.method,
                    "method": "grpc",
                    "client_ip": context.peer(),
                }
            )
            start = time.perf_counter()
            logger = get_logger()
            try:
                response = await inner(request, context)
            except Exception as exc:
                logger.error(
                    "the request failed",
                    path=details.method,
                    method="grpc",
                    status=str(context.code()),
                    error=str(exc),
                    **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
                )
                request_ctx.reset(token)
                raise
            logger.info(
                "request",
                path=details.method,
                method="grpc",
                status=str(context.code() or grpc.StatusCode.OK),
                **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
            )
            request_ctx.reset(token)
            return response

        return behavior


    def _wrap_stream(self, inner, details):
        """The same logging context; what changes is WHAT is measured.

        In a unary call, `duration_ms` is the time to process one message. In a
        stream, it is the CONNECTION's duration — and that is why there are two
        lines, not one: "stream opened" at the opening (or a six-hour
        subscription would only show up in the log six hours later, and nobody
        would know it exists) and "stream" at the close, with the duration and
        how many items went out.
        """

        async def behavior(request, context):
            md = _metadata(context)
            request_id = md.get("x-request-id") or uuid.uuid4().hex
            token = request_ctx.set(
                {
                    "request_id": request_id,
                    "path": details.method,
                    "method": "grpc",
                    "client_ip": context.peer(),
                }
            )
            start = time.perf_counter()
            logger = get_logger()
            sent = 0
            logger.info("stream opened", path=details.method, method="grpc")
            try:
                async for response in inner(request, context):
                    sent += 1
                    yield response
            except Exception as exc:
                logger.error(
                    "the stream failed",
                    path=details.method,
                    method="grpc",
                    status=str(context.code()),
                    error=str(exc),
                    sent=sent,
                    **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
                )
                raise
            else:
                logger.info(
                    "stream",
                    path=details.method,
                    method="grpc",
                    status=str(context.code() or grpc.StatusCode.OK),
                    sent=sent,
                    **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
                )
            finally:
                # It also covers the client that gives up midway: the generator
                # is finalized, the `finally` runs, and the context is not left
                # hanging.
                _reset(request_ctx, token)

        return behavior


class ErrorInterceptor(_Interceptor):
    """Translates an exception into a gRPC status — the same semantics REST returns.

    A core error CROSSES with its own status (NOT_FOUND stays NOT_FOUND) and with
    the detail passed through the writer REST already uses: a 5xx detail leaks
    through neither of the two ports.
    """

    def _wrap(self, inner, details):
        async def behavior(request, context):
            try:
                return await inner(request, context)
            except AioRpcError as exc:
                code, detail = as_grpc(exc)
            except HTTPException as exc:
                # The security decorators speak HTTPException — they were born
                # in REST and still speak it. Translating on the way out is what
                # allows reusing them without touching the already tested HTTP
                # behaviour.
                code, detail = as_grpc_from_http(exc)
            except ValidationError as exc:
                # A malformed message is INVALID_ARGUMENT, the gRPC equivalent
                # of the 422 FastAPI returns for the same case.
                code, detail = grpc.StatusCode.INVALID_ARGUMENT, str(exc)
            except Exception as exc:
                # A failure of ours, not the client's: it records the whole thing
                # in the log and returns a generic one on the wire, for the same
                # reason as the 5xx writer.
                get_logger().error("unhandled error in the servicer", error=str(exc))
                code, detail = grpc.StatusCode.INTERNAL, "internal error"
            await context.abort(code, detail)

        return behavior


    def _wrap_stream(self, inner, details):
        """An error AFTER the first item is still translatable — and this is where it is done.

        The difference from HTTP is large and worth recording: in REST, a status
        already sent cannot be swapped, and that is why `app/routers/stream.py`
        has to convert the error into an SSE event. In gRPC the status travels in
        the TRAILERS, at the end of the call — so `context.abort` still works
        with half the stream already delivered, and the client receives the right
        code instead of a stream that ends with no explanation.
        """

        async def behavior(request, context):
            try:
                async for response in inner(request, context):
                    yield response
                return
            except AioRpcError as exc:
                code, detail = as_grpc(exc)
            except HTTPException as exc:
                code, detail = as_grpc_from_http(exc)
            except ValidationError as exc:
                code, detail = grpc.StatusCode.INVALID_ARGUMENT, str(exc)
            except Exception as exc:
                get_logger().error("unhandled error in the stream", error=str(exc))
                code, detail = grpc.StatusCode.INTERNAL, "internal error"
            await context.abort(code, detail)

        return behavior


class AuthInterceptor(_Interceptor):
    """Verifies the token and resolves the active account — the AuthMiddleware, in gRPC.

    It fills in the SAME `auth_ctx` REST fills in, with the SAME resolver. The
    decorators (@account_scoped, @require_role, @require_grant) read from there
    and have no way of knowing which port the call came in through — which is
    exactly the point.
    """

    def __init__(self, verifier: FirebaseVerifier, resolver=None):
        self.verifier = verifier
        # resolver(principal, account_id, raw_token="") -> (user_id, role, grants)
        # The one that decides roles and grants is the CORE (ADR-0016); the BFF
        # asks.
        self.resolver = resolver

    def _wrap(self, inner, details):
        # @public on the servicer's method exempts it from authentication, as in
        # REST. functools.wraps propagates the marker through @log and the other
        # decorators, so the order in which it is stacked does not matter.
        if getattr(inner, "__is_public__", False):
            return inner

        async def behavior(request, context):
            # The two wrappers (unary and stream) resolve identity through the
            # SAME pair of methods: duplicating the token reading here would be
            # the classic way for the two ports to diverge with nobody noticing.
            principal, raw_token, account_id = await self._authenticate(_metadata(context))
            user_id, role, grants = await self._resolve(principal, account_id, raw_token)
            token = auth_ctx.set(
                AuthContext(
                    principal=principal,
                    raw_token=raw_token,
                    user_id=user_id,
                    account_id=account_id,
                    role=role,
                    grants=grants,
                )
            )
            try:
                return await inner(request, context)
            finally:
                auth_ctx.reset(token)

        return behavior

    def _wrap_stream(self, inner, details):
        """Authentication resolved ONCE, at the stream's opening.

        Revalidating on every item would be expensive (two calls to the core per
        event) and also wrong: what decides the session's lifetime is the token's
        validity, checked when the connection is made — as happens with an
        ordinary HTTP connection, which does not reauthenticate on every byte
        sent. A client whose token is expiring reconnects; it is what SSE already
        does on its own.
        """
        if getattr(inner, "__is_public__", False):
            return inner

        async def behavior(request, context):
            principal, raw_token, account_id = await self._authenticate(_metadata(context))
            user_id, role, grants = await self._resolve(principal, account_id, raw_token)
            token = auth_ctx.set(
                AuthContext(
                    principal=principal,
                    raw_token=raw_token,
                    user_id=user_id,
                    account_id=account_id,
                    role=role,
                    grants=grants,
                )
            )
            try:
                async for response in inner(request, context):
                    yield response
            finally:
                _reset(auth_ctx, token)

        return behavior

    async def _authenticate(self, md: dict[str, str]):
        """Token → Principal, the raw token, and the active account. Common to both forms.

        The raw token comes back too because the core verifies it itself
        (ADR-0029): the edge proves WHO by forwarding the proof, not by
        asserting the conclusion.
        """
        # Identity comes from the TOKEN, not from the message's body. That is
        # why no request of the edge's contract has a CallContext.
        raw = md.get("authorization", "")
        if not raw:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            principal = await self.verifier.verify(raw)
        except InvalidToken as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        # The active account comes from the metadata — it is the interface's
        # selector, the same x-account-id header as REST.
        return principal, raw.removeprefix("Bearer ").strip(), md.get("x-account-id", "")

    async def _resolve(self, principal, account_id: str, raw_token: str = ""):
        if self.resolver is None:
            return "", "", {}
        try:
            return await self.resolver(principal, account_id, raw_token)
        except HTTPException:
            # Already translated (and written) by the resolver's `as_http`: it
            # goes on to the ErrorInterceptor to become a gRPC status.
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Failed to resolve the account") from exc
