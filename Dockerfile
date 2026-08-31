# dop-api — BFF da plataforma DOP (REST+SSE para o cockpit, gRPC para CLI).
# Multi-stage: o uv resolve o ambiente na build; a imagem final não tem
# compilador, nem gerenciador de pacote, nem cache de wheels.

# ── build ────────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS build

# O uv vem como binário estático da imagem oficial — nada de pip install de
# gerenciador de pacote dentro do build.
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# --no-install-project: instala SÓ as dependências, sem o pacote da aplicação.
# O código entra por COPY na camada seguinte — assim mexer numa rota não
# invalida a resolução do lockfile.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ── runtime ──────────────────────────────────────────────────────────────────
FROM python:3.13-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="dop-api" \
      org.opencontainers.image.version="${VERSION}"

# PYTHONDONTWRITEBYTECODE: usuário arbitrário não escreve .pyc no /app (o
# bytecode já foi compilado na build). PYTHONUNBUFFERED: sem isso o log JSON
# fica preso no buffer e o `kubectl logs` mostra o pod mudo.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    HOME=/app

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app

# Usuário arbitrário não-root (requisito OKD/OpenShift — spec do substrato §2).
# O OKD atribui um UID que NÃO existe em /etc/passwd e coloca o processo no
# grupo 0; por isso a permissão vive no grupo root, nunca num usuário nomeado.
RUN chgrp -R 0 /app && chmod -R g=u /app

USER 1001
# Duas portas de entrada, um processo: HTTP para o cockpit, gRPC para o
# dop-cli e os agentes. A porta gRPC é aberta pelo lifespan do FastAPI, então
# o comando abaixo sobe as duas.
EXPOSE 8000 9095

# Um worker por pod: escala é réplica do Deployment, não processo dentro do
# container — o SSE precisa saber em qual processo a conexão está.
#
# --log-level warning cala o logger PRÓPRIO do uvicorn, que tem formatador
# próprio e sairia em texto puro no meio do JSON do structlog. Erro e aviso do
# servidor continuam aparecendo; a linha de requisição é responsabilidade do
# LoggingMiddleware, que já escreve no formato canônico.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--log-level", "warning"]
