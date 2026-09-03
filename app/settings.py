"""The BFF's configuration, resolved from the environment."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "dop-api"
    log_level: str = "info"
    http_port: int = 8000
    grpc_port: int = 9095  # the gRPC server for the CLI and the sandbox
    # Turning the gRPC port off is for the tests (which create the server
    # themselves, on an ephemeral port) and for whoever brings the BFF up as
    # REST only.
    grpc_enabled: bool = True

    core_grpc: str = "dop-core.dop-local.svc:9090"
    core_deadline_s: float = 10.0
    # An agent turn is LONG: a call to a reasoning model takes minutes, and the
    # core's normal deadline would kill every real turn.
    agent_turn_deadline_s: float = 600.0

    # The key the edge signs its assertions to the core with (ADR-0029). Empty
    # means the edge signs nothing — which only works while the core runs in
    # `permissive`, and is exactly what the migration needs.
    call_auth_key: str = ""

    firebase_project: str = "dop-local"
    firebase_auth_emulator_host: str = ""
    firebase_storage_emulator_host: str = ""
    storage_emulator_host: str = ""

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # ── SSE (app/routers/stream.py) ────────────────────────────────────────
    # The interval of the `: ping` comment. A proxy or load balancer drops an
    # idle connection, and an event stream spends most of its time idle — with
    # no traffic at all between two happenings. 15s is half of the TIGHTEST idle
    # timeout the platform meets on the path (30s on GCP's HTTP(S) load
    # balancer; nginx and k3s' ingress use 60s): half gives room for one ping to
    # be lost without the connection dropping. Anything smaller would only drain
    # a phone's battery for nothing.
    sse_ping_s: int = 15
    # `retry:` sent ONCE on opening — it is the reconnection interval the
    # browser's EventSource comes to use (its own default varies per browser).
    # 3s: fast enough for the cockpit not to look frozen, slow enough not to
    # become a reconnection storm when the core goes down.
    sse_retry_ms: int = 3000


settings = Settings()
