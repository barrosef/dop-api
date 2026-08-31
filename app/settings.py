"""Configuração do BFF, resolvida do ambiente."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "dop-api"
    log_level: str = "info"
    http_port: int = 8000
    grpc_port: int = 9095  # servidor gRPC para CLI e sandbox

    core_grpc: str = "dop-core.dop-local.svc:9090"
    core_deadline_s: float = 10.0

    firebase_project: str = "dop-local"
    firebase_auth_emulator_host: str = ""
    firebase_storage_emulator_host: str = ""
    storage_emulator_host: str = ""

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]


settings = Settings()
