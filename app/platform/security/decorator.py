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


def public(func):
    """Marks the handler as exempt from authentication."""
    func.__is_public__ = True
    return func


def register_public_routes(routes) -> None:
    """Sweeps the routes and registers the patterns of the @public handlers.

    It converts a path template (/{id}/x) into a regex, so a parameterized route
    is recognized at request time. Call it ONCE, after registering everything.
    """
    _PUBLIC_PATTERNS.clear()
    for route in routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or not getattr(endpoint, "__is_public__", False):
            continue
        path = getattr(route, "path", "")
        if not path:
            continue
        pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        for method in getattr(route, "methods", set()):
            _PUBLIC_PATTERNS.append((method.upper(), pattern))


def is_public(request: Request) -> bool:
    path, method = request.url.path, request.method.upper()
    return any(m == method and p.match(path) for m, p in _PUBLIC_PATTERNS)


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
