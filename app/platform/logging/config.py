"""Log estruturado em JSON — MESMO formato do core (dop-core).

Os dois processos escrevem os mesmos nomes de campo, o mesmo formato de tempo e
a mesma política de máscara. Um log agregado de plataforma só é útil se as duas
pontas falarem a mesma língua.
"""

import logging
import os
import sys

import structlog

# Campos canônicos — espelham internal/platform/logging do core.
FIELD_REQUEST_ID = "request_id"
FIELD_ACCOUNT_ID = "account_id"
FIELD_ACTOR_ID = "actor_id"
FIELD_COMPONENT = "component"
FIELD_DURATION_MS = "duration_ms"
FIELD_ERROR = "error"

# Redação de segredos é REQUISITO (F-10), não conveniência.
AUTO_MASK: frozenset[str] = frozenset(
    {
        "password",
        "token",
        "secret",
        "authorization",
        "api_key",
        "private_key",
        "client_secret",
        "credential",
        "id_token",
        "refresh_token",
        "cpf",
        "cnpj",
    }
)
REDACTED = "***"


def _mask_processor(_logger, _method, event_dict: dict) -> dict:
    """Aplica a máscara em qualquer profundidade do evento."""

    def walk(value):
        if isinstance(value, dict):
            return {
                k: (REDACTED if k.lower() in AUTO_MASK else walk(v)) for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(event_dict)


def _add_context(_logger, _method, event_dict: dict) -> dict:
    """Injeta request_id e account_id em toda linha, sem o chamador pedir."""
    from app.platform.context import auth_ctx, request_ctx

    ctx = request_ctx.get()
    if rid := ctx.get("request_id"):
        event_dict.setdefault(FIELD_REQUEST_ID, rid)
    if auth := auth_ctx.get():
        event_dict.setdefault(FIELD_ACCOUNT_ID, auth.account_id)
        event_dict.setdefault(FIELD_ACTOR_ID, auth.user_id)
    return event_dict


def configure() -> None:
    level = os.getenv("LOG_LEVEL", "info").upper()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            _mask_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial):
    """Devolve um logger JÁ configurado.

    Chamar SEMPRE no ponto de uso, nunca no import do módulo: `configure()` roda
    no lifespan, e um logger vinculado antes disso congela a configuração padrão
    — as linhas sairiam fora do formato JSON, silenciosamente.
    """
    return structlog.get_logger().bind(component="dop-api", **initial)
