# dop-api — the DOP platform's BFF (REST+SSE for the cockpit, gRPC for the CLI).
# Multi-stage: uv resolves the environment at build time; the final image has no
# compiler, no package manager and no wheel cache.

# ── build ────────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS build

# uv comes as a static binary from the official image — no pip-installing a
# package manager inside the build.
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# --no-install-project: it installs ONLY the dependencies, without the
# application's package. The code comes in through a COPY in the next layer — so
# touching a route does not invalidate the lockfile's resolution.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ── runtime ──────────────────────────────────────────────────────────────────
FROM python:3.13-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="dop-api" \
      org.opencontainers.image.version="${VERSION}"

# PYTHONDONTWRITEBYTECODE: an arbitrary user does not write .pyc into /app (the
# bytecode was already compiled at build time). PYTHONUNBUFFERED: without it the
# JSON log stays stuck in the buffer and `kubectl logs` shows a mute pod.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    HOME=/app

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app

# An arbitrary non-root user (an OKD/OpenShift requirement — the substrate spec
# §2). OKD assigns a UID that does NOT exist in /etc/passwd and puts the process
# in group 0; that is why the permission lives on the root group, never on a
# named user.
RUN chgrp -R 0 /app && chmod -R g=u /app

USER 1001
# Two doors in, one process: HTTP for the cockpit, gRPC for dop-cli and the
# agents. The gRPC port is opened by FastAPI's lifespan, so the command below
# brings both up.
EXPOSE 8000 9095

# One worker per pod: scale is a Deployment replica, not a process inside the
# container — SSE needs to know which process a connection is on.
#
# --log-level warning silences uvicorn's OWN logger, which has its own formatter
# and would come out as plain text in the middle of structlog's JSON. The
# server's errors and warnings still appear; the request line is
# LoggingMiddleware's responsibility, and it already writes in the canonical
# format.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--log-level", "warning"]
