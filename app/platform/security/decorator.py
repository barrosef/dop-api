"""Decorators de autorização — leem o ContextVar, não recebem parâmetro.

    @public                       isenta de autenticação
    @account_scoped               exige conta ativa (regra do SP-0)
    @require_role("admin")        papel na conta ativa
    @require_grant("use")         concessão sobre um recurso (ADR-0013)
"""

import asyncio
import functools
import re

from fastapi import HTTPException
from starlette.requests import Request

from app.platform.context import auth_ctx

_PUBLIC_PATTERNS: list[tuple[str, re.Pattern]] = []


def public(func):
    """Marca o handler como isento de autenticação."""
    func.__is_public__ = True
    return func


def register_public_routes(routes) -> None:
    """Varre as rotas e registra os padrões dos handlers marcados @public.

    Converte template de caminho (/{id}/x) em regex, para rota parametrizada
    ser reconhecida em tempo de requisição. Chamar UMA vez, após registrar tudo.
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
        raise HTTPException(status_code=401, detail="Não autenticado")
    return ctx


def _wrap(check):
    """Constrói um decorator que aplica `check` ao AuthContext."""

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
    """Exige conta ativa.

    Materializa a regra do SP-0 — *requisição sem conta ativa é inválida* —
    como decorator, em vez de checagem espalhada por cada handler.
    """

    def check(ctx):
        if not ctx.account_id:
            raise HTTPException(status_code=400, detail="Nenhuma conta ativa selecionada")

    return _wrap(check)(func)


def require_role(*roles: str):
    """Exige um dos papéis na conta ativa."""

    def check(ctx):
        if not ctx.account_id:
            raise HTTPException(status_code=400, detail="Nenhuma conta ativa selecionada")
        if not ctx.has_role(*roles):
            raise HTTPException(status_code=403, detail="Permissão insuficiente")

    return _wrap(check)


def require_grant(level: str = "use", *, param: str = "resource_id"):
    """Exige concessão sobre o recurso identificado por `param` na chamada.

    `manage` satisfaz um requisito de `use`; owner e admin têm manage implícito.
    """
    ranking = {"use": 1, "manage": 2}
    needed = ranking.get(level, 1)

    def decorator(func):
        def check_with(kwargs):
            ctx = _ctx_or_401()
            resource_id = kwargs.get(param)
            if not resource_id:
                raise HTTPException(status_code=400, detail=f"Parâmetro {param} ausente")
            have = ranking.get(ctx.grant_level(str(resource_id)) or "", 0)
            if have < needed:
                raise HTTPException(
                    status_code=403, detail=f"Sem concessão '{level}' sobre o recurso"
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
