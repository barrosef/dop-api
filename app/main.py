"""dop-api — BFF da plataforma DOP.

Duas portas de entrada, um só núcleo atrás:
  REST + SSE  → o frontend (dop-app)
  gRPC        → o dop-cli e os agentes dos sandboxes

O que este processo NÃO faz: falar com banco. Todo estado vem do dop-core
(ADR-0016).
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from grpc.aio import AioRpcError

from app.coreclient.client import core
from app.coreclient.resolver import CoreResolver
from app.platform.errors import grpc_exception_handler
from app.platform.logging.config import configure as configure_logging
from app.platform.logging.config import get_logger
from app.platform.logging.middleware import LoggingMiddleware
from app.platform.security.decorator import register_public_routes
from app.platform.security.firebase import FirebaseVerifier
from app.platform.security.middleware import AuthMiddleware
from app.routers import health, identity
from app.settings import settings


def _bridge_storage_emulator() -> None:
    """Ponte STORAGE_EMULATOR_HOST ↔ FIREBASE_STORAGE_EMULATOR_HOST.

    O SDK do Cloud Storage lê a primeira; o Firebase CLI expõe a segunda. Sem
    esta ponte, upload local vai para o bucket REAL (ADR-0020 §4).
    """
    fb = os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
    if fb and not os.getenv("STORAGE_EMULATOR_HOST"):
        os.environ["STORAGE_EMULATOR_HOST"] = fb if fb.startswith("http") else f"http://{fb}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    _bridge_storage_emulator()
    log = get_logger()

    await core.start()
    log.info("núcleo conectado", target=core.target)

    # Precisa rodar DEPOIS de todas as rotas registradas.
    register_public_routes(app.routes)

    verifier = FirebaseVerifier(settings.firebase_project)
    if verifier.using_emulator:
        log.info("identidade apontada para o emulador do Firebase Auth")
    app.state.verifier = verifier

    log.info("dop-api pronto", http_port=settings.http_port)
    yield
    await core.stop()
    log.info("dop-api encerrado")


def create_app() -> FastAPI:
    app = FastAPI(
        title="DOP — BFF",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Borda da plataforma DOP. REST+SSE para o cockpit, gRPC para CLI e agentes. "
            "Todo estado vive no dop-core."
        ),
    )

    app.include_router(health.router)
    app.include_router(identity.router)

    verifier = FirebaseVerifier(settings.firebase_project)
    # Ordem importa: logging abre o contexto, auth preenche o principal.
    # O resolver é quem pergunta ao CORE user_id, papel e concessões — o BFF não
    # decide permissão, ele traduz a decisão do núcleo (ADR-0016).
    app.add_middleware(AuthMiddleware, verifier=verifier, resolver=CoreResolver())
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )

    app.add_exception_handler(AioRpcError, grpc_exception_handler)
    return app


app = create_app()
