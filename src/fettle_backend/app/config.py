from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "postgresql+asyncpg://fettle:fettle@localhost:5433/fettle_backend"
    internal_api_enabled: bool = False
    internal_api_key: str = "change-me"
    legacy_auth_secret: str = "change-me"
    artifact_storage_root: str = "./var/artifacts"
    pdf_delivery_public_base_url: str = "http://localhost:8000"
    pdf_delivery_token_secret: str | None = None
    pdf_delivery_token_ttl_seconds: int = 900
    fettle_sync_enabled: bool = False
    fettle_backend_url: str | None = None
    fettle_sync_secret: str | None = None
    fettle_sync_timeout_seconds: float = 8
    fettle_sync_max_attempts: int = 10
    fettle_sync_retry_delay_seconds: float = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
