"""Structured JSON logging — the SAME format as the core's (dop-core).

Both processes write the same field names, the same time format and the same
masking policy. An aggregated platform log is only useful if the two ends speak
the same language.
"""

import logging
import os
import sys

import structlog

# The canonical fields — they mirror the core's internal/platform/logging.
FIELD_REQUEST_ID = "request_id"
FIELD_ACCOUNT_ID = "account_id"
FIELD_ACTOR_ID = "actor_id"
FIELD_COMPONENT = "component"
FIELD_DURATION_MS = "duration_ms"
FIELD_ERROR = "error"

# Redacting secrets is a REQUIREMENT (F-10), not a convenience.
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
    """Applies the mask at any depth of the event."""

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
    """Injects request_id and account_id into every line, without the caller asking."""
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
    """Returns a logger that is ALREADY configured.

    ALWAYS call it at the point of use, never at the module's import:
    `configure()` runs in the lifespan, and a logger bound before that freezes
    the default configuration — the lines would come out outside the JSON format,
    silently.
    """
    return structlog.get_logger().bind(component="dop-api", **initial)
