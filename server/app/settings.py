from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "info"

    secret_key: str
    database_url: str = "postgresql+asyncpg://app:change-me@db:5432/app"
    postgres_user: str = "app"
    postgres_password: str = "change-me"
    postgres_db: str = "app"

    public_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:5173"
    preview_host: str = "http://preview.localhost:8000"

    max_runs_per_project: int = 100

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment_planner: str = ""
    azure_openai_deployment_coder: str = ""
    azure_openai_deployment_reviewer: str = ""

    rate_limit_enabled: bool = True
    trusted_proxy_cidrs: str = ""

    data_dir: Path = Path("data")
    static_dir: Path = Path("static")
    node_binary: str = "node"
    js_check_timeout_seconds: float = 5.0
    worker_poll_seconds: float = 1.0
    worker_heartbeat_seconds: float = 15.0
    worker_stale_seconds: float = 60.0

    @field_validator("secret_key")
    @classmethod
    def secret_key_not_placeholder(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("SECRET_KEY must not be empty")
        return value

    @property
    def psycopg_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    @property
    def trusted_proxy_networks(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_cidrs.split(",") if item.strip()]
