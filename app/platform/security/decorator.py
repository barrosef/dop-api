"""Authorization decorators — they read the ContextVar, they take no parameter.

    @public                       exempt from authentication
    @account_scoped               requires an active account (SP-0's rule)
    @require_role("admin")        a role in the active account
    @require_grant("use")         a grant over a resource (ADR-0013)
"""

import asyncio
import functools
import re

from fastapi import HTTPException
from starlette.requests import Request

from app.platform.context import auth_ctx

_PUBLIC_PATTERNS: list[tuple[str, re.Pattern]] = []
_TOKEN_ONLY_PATTERNS: list[tuple[str, re.Pattern]] = []


def public(func):
    """Marks the handler as exempt from authentication."""
    func.__is_public__ = True
    return func


def token_only(func):
    """The token is verified; NOTHING is resolved from the core.

    There is a narrow set of routes that need to know who is asking and cannot
    ask the core who that is — because the core does not know yet. Sign-up by
    e-mail and password is the case: the core refuses an unverified password
    credential before creating anything (spec SP-0 D-5), so resolving the user
    would answer 412 to the very request that exists to fix it.

    It is NOT @public. @public skips authentication entirely, and a route that
    mails a link to an address must know the address belongs to whoever is
    asking. Here the token is verified exactly as everywhere else; only the trip
    to the core is skipped.
    """
    func.__is_token_only__ = True
    return func


def _flatten(routes):
    """Yields the leaf routes, walking into included routers.

    FastAPI does not necessarily flatten `include_router` into `app.routes`:
    depending on the version, what lands there is a wrapper holding its own
    `.routes`. Sweeping only the top level then finds NO endpoint at all, and
    every marker registered by this module silently registers nothing.

    It fails closed — a @public route merely goes on demanding a token — which
    is why it can sit there unnoticed. `/healthz` kept working the whole time
    because the middleware also carries a hardcoded list of paths, so the one
    symptom anybody would have looked for was absent.
    """
    for route in routes:
        # Two shapes, because the wrapper's name for its children has changed
        # between FastAPI versions: `.routes` on some, `.original_router.routes`
        # on others. Reading both is cheaper than pinning a version over it.
        nested = getattr(route, "routes", None)
        if nested is None:
            inner = getattr(route, "original_router", None)
            nested = getattr(inner, "routes", None) if inner is not None else None
        if nested:
            yield from _flatten(nested)
        else:
            yield route


def register_public_routes(routes) -> None:
    """Sweeps the routes and registers the patterns of the @public handlers.

    It converts a path template (/{id}/x) into a regex, so a parameterized route
    is recognized at request time. Call it ONCE, after registering everything.
    """
    _PUBLIC_PATTERNS.clear()
    _TOKEN_ONLY_PATTERNS.clear()
    for route in _flatten(routes):
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        if getattr(endpoint, "__is_public__", False):
            target = _PUBLIC_PATTERNS
        elif getattr(endpoint, "__is_token_only__", False):
            target = _TOKEN_ONLY_PATTERNS
        else:
            continue
        path = getattr(route, "path", "")
        if not path:
            continue
        pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        for method in getattr(route, "methods", set()):
            target.append((method.upper(), pattern))


def is_public(request: Request) -> bool:
    path, method = request.url.path, request.method.upper()
    return any(m == method and p.match(path) for m, p in _PUBLIC_PATTERNS)


def is_token_only(request: Request) -> bool:
    path, method = request.url.path, request.method.upper()
    return any(m == method and p.match(path) for m, p in _TOKEN_ONLY_PATTERNS)


def _ctx_or_401():
    ctx = auth_ctx.get()
    if ctx is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return ctx


def _wrap(check):
    """Builds a decorator that applies `check` to the AuthContext."""

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            check(_ctx_or_401())
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            check(_ctx_or_401())
            return func(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


def account_scoped(func):
    """Requires an active account.

    It materializes SP-0's rule — *a request with no active account is
    invalid* — as a decorator, instead of a check scattered through every
    handler.
    """

    def check(ctx):
        if not ctx.account_id:
            raise HTTPException(status_code=400, detail="No active account selected")

    return _wrap(check)(func)


def require_role(*roles: str):
    """Requires one of the roles in the active account."""

    def check(ctx):
        if not ctx.account_id:
            raise HTTPException(status_code=400, detail="No active account selected")
        if not ctx.has_role(*roles):
            raise HTTPException(status_code=403, detail="Insufficient permission")

    return _wrap(check)


def require_grant(level: str = "use", *, param: str = "resource_id"):
    """Requires a grant over the resource `param` identifies in the call.

    `manage` satisfies a requirement of `use`; owner and admin have implicit
    manage.
    """
    ranking = {"use": 1, "manage": 2}
    needed = ranking.get(level, 1)

    def decorator(func):
        def check_with(kwargs):
            ctx = _ctx_or_401()
            resource_id = kwargs.get(param)
            if not resource_id:
                raise HTTPException(status_code=400, detail=f"Missing parameter {param}")
            have = ranking.get(ctx.grant_level(str(resource_id)) or "", 0)
            if have < needed:
                raise HTTPException(
                    status_code=403, detail=f"No '{level}' grant over the resource"
                )

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            check_with(kwargs)
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            check_with(kwargs)
            return func(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator
