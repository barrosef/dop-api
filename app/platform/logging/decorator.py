"""@log — entrada, saída, erro e duração, sem poluir o código de negócio.

Padrão: o decorator lê o contexto do ContextVar; o método decorado não recebe
nada a mais. Funciona em função síncrona e assíncrona.
"""

import asyncio
import functools
import inspect
import time

from app.platform.logging.config import (
    AUTO_MASK,
    FIELD_DURATION_MS,
    FIELD_ERROR,
    REDACTED,
    get_logger,
)


def _class_name(args: tuple) -> str | None:
    if args:
        cls = type(args[0])
        if cls.__module__ != "builtins":
            return cls.__name__
    return None


def _masked_params(func, args: tuple, kwargs: dict, extra: list[str]) -> dict:
    masked = AUTO_MASK | set(extra)
    try:
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        return {}
    return {
        name: REDACTED if name.lower() in masked else repr(value)
        for name, value in bound.arguments.items()
        if name != "self"
    }


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def log(_func=None, *, level: str | None = None, mask: list[str] | None = None):
    """
    @log
    @log(level="debug")            # inclui os parâmetros, já mascarados
    @log(mask=["cpf", "cnpj"])     # acrescenta chaves à máscara automática
    """

    def decorator(func):
        method = func.__name__
        include_params = (level or "").lower() == "debug"

        def _entry(args, kwargs):
            entry = {"method": method}
            if cls := _class_name(args):
                entry["class"] = cls
            if include_params:
                entry["params"] = _masked_params(func, args, kwargs, mask or [])
            return entry

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            get_logger().info("call", **_entry(args, kwargs))
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                get_logger().error(
                    "error",
                    method=method,
                    **{FIELD_ERROR: str(exc), FIELD_DURATION_MS: _elapsed_ms(start)},
                )
                raise
            get_logger().info("return", method=method, **{FIELD_DURATION_MS: _elapsed_ms(start)})
            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            get_logger().info("call", **_entry(args, kwargs))
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                get_logger().error(
                    "error",
                    method=method,
                    **{FIELD_ERROR: str(exc), FIELD_DURATION_MS: _elapsed_ms(start)},
                )
                raise
            get_logger().info("return", method=method, **{FIELD_DURATION_MS: _elapsed_ms(start)})
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator(_func) if _func is not None else decorator
