"""dop-api — the DOP platform's BFF.

Two doors in, a single core behind them:
  REST + SSE  → the frontend (dop-app)
  gRPC        → dop-cli and the sandboxes' agents

What this process does NOT do: talk to a database. All state comes from dop-core
(ADR-0016).
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from grpc.aio import AioRpcError

from app.coreclient.client import core
from app.coreclient.resolver import CoreResolver
from app.grpcapi.server import GrpcServer
from app.platform.errors import grpc_exception_handler
from app.platform.logging.config import configure as configure_logging
from app.platform.logging.config import get_logger
from app.platform.logging.middleware import LoggingMiddleware
from app.platform.security.decorator import register_public_routes
from app.platform.security.firebase import FirebaseVerifier
from app.platform.security.middleware import AuthMiddleware
from app.routers import (
    attention,
    cost,
    delivery,
    demand,
    execution,
    health,
    hierarchy,
    identity,
    knowledge,
    resource,
    runtime,
    secondfactor,
    stream,
    workflow,
)
from app.settings import settings


def _bridge_storage_emulator() -> None:
    """The STORAGE_EMULATOR_HOST ↔ FIREBASE_STORAGE_EMULATOR_HOST bridge.

    The Cloud Storage SDK reads the first; the Firebase CLI exposes the second.
    Without this bridge, a local upload goes to the REAL bucket (ADR-0020 §4).
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
    log.info("core connected", target=core.target)

    # It has to run AFTER every route is registered.
    register_public_routes(app.routes)

    verifier = FirebaseVerifier(settings.firebase_project)
    if verifier.using_emulator:
        log.info("identity pointed at the Firebase Auth emulator")
    app.state.verifier = verifier

    # The gRPC port comes up AFTER the channel to the core (every use case
    # depends on it) and goes down BEFORE it, so the graceful shutdown can still
    # finish the in-flight calls. The same process, the same use cases, the same
    # authentication — only the adapter changes.
    grpc_server = None
    if settings.grpc_enabled:
        grpc_server = GrpcServer(
            verifier=verifier, resolver=CoreResolver(), port=settings.grpc_port
        )
        await grpc_server.start()
    app.state.grpc_server = grpc_server

    log.info(
        "dop-api ready",
        http_port=settings.http_port,
        grpc_port=grpc_server.port if grpc_server else 0,
    )
    yield
    if grpc_server is not None:
        await grpc_server.stop()
    await core.stop()
    log.info("dop-api stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="DOP — BFF",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "The DOP platform's edge. REST+SSE for the cockpit, gRPC for the CLI "
            "and the agents. All state lives in dop-core."
        ),
    )

    app.include_router(health.router)
    app.include_router(identity.router)
    app.include_router(secondfactor.router)
    app.include_router(hierarchy.router)
    app.include_router(resource.router)
    # The work cycle.
    app.include_router(workflow.router)
    app.include_router(demand.router)
    app.include_router(delivery.router)
    # Knowledge, cost and the substrate.
    app.include_router(knowledge.router)
    app.include_router(cost.router)
    app.include_router(execution.router)
    app.include_router(runtime.router)
    # The attention box: the single queue of "where am I needed".
    app.include_router(attention.router)
    # SSE last: it is the only one that opens a long connection, and leaving it
    # at the end keeps the assembly readable in the order the cockpit consumes.
    app.include_router(stream.router)

    verifier = FirebaseVerifier(settings.firebase_project)
    # The order matters: logging opens the context, auth fills in the principal.
    # The resolver is the one that asks the CORE for the user_id, the role and
    # the grants — the BFF does not decide permission, it translates the core's
    # decision (ADR-0016).
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
