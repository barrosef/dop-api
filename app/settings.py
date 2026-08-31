"""Configuração do BFF, resolvida do ambiente."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "dop-api"
    log_level: str = "info"
    http_port: int = 8000
    grpc_port: int = 9095  # servidor gRPC para CLI e sandbox
    # Desligar a porta gRPC é para o teste (que cria o servidor por conta
    # própria, em porta efêmera) e para quem sobe o BFF só como REST.
    grpc_enabled: bool = True

    core_grpc: str = "dop-core.dop-local.svc:9090"
    core_deadline_s: float = 10.0

    firebase_project: str = "dop-local"
    firebase_auth_emulator_host: str = ""
    firebase_storage_emulator_host: str = ""
    storage_emulator_host: str = ""

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # ── SSE (app/routers/stream.py) ────────────────────────────────────────
    # Intervalo do comentário `: ping`. Proxy e balanceador derrubam conexão
    # ociosa, e um stream de eventos passa a maior parte do tempo ocioso — sem
    # tráfego nenhum entre dois acontecimentos. 15s é metade do timeout ocioso
    # mais APERTADO que a plataforma encontra no caminho (30s do balanceador
    # HTTP(S) do GCP; nginx e o ingress do k3s usam 60s): a metade dá margem
    # para um ping se perder sem que a conexão caia. Menor que isso só gastaria
    # bateria de celular à toa.
    sse_ping_s: int = 15
    # `retry:` enviado UMA vez na abertura — é o intervalo de reconexão que o
    # EventSource do browser passa a usar (o padrão dele varia por navegador).
    # 3s: rápido o bastante para o cockpit não parecer travado, lento o
    # bastante para não virar tempestade de reconexão quando o núcleo cai.
    sse_retry_ms: int = 3000


settings = Settings()
