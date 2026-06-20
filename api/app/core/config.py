from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ App
    app_name: str = "Fettle API"
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ------------------------------------------------------------------ Database
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/fettle_dev"
    )

    # ------------------------------------------------------------------ JWT
    jwt_secret_key: str = Field(default=...)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 120

    # ------------------------------------------------------------------ Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"
    celery_timezone: str = "UTC"

    # ------------------------------------------------------------------ LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_room_name: str = "default"

    # ------------------------------------------------------------------ Vobiz
    vobiz_api_url: str = "https://api.vobiz.ai"
    vobiz_api_key: str = ""

    # ------------------------------------------------------------------ S3 / AWS
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-south-1"
    s3_bucket_name: str = "fettle-media"

    # ------------------------------------------------------------------ LLM
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o"
    openai_api_key: str = ""

    # ------------------------------------------------------------------ Concurrency limits
    call_concurrency_limit: int = 3

    # ------------------------------------------------------------------ Internal API
    internal_api_shared_secret: str = ""
    internal_api_base_url: str = "http://api:8000"

    # ------------------------------------------------------------------ PayPal
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_api_base: str = "https://api-m.sandbox.paypal.com"
    consultation_price_usd: float = 15.00

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def _require_jwt_secret(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be set and at least 32 characters. "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy async engine URL (psycopg3 async dialect)."""
        url = self.database_url
        if "postgresql+psycopg_async://" in url:
            return url
        if "postgresql+psycopg://" in url:
            return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://")
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg_async://")
        return url

    @property
    def sync_database_url(self) -> str:
        """SQLAlchemy sync engine URL — used by Alembic migrations."""
        url = self.database_url
        if "postgresql+psycopg_async://" in url:
            return url.replace("postgresql+psycopg_async://", "postgresql+psycopg://")
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://")
        return url

    @property
    def test_database_url(self) -> str:
        """Replaces DB name with <name>_test for the test suite."""
        import re

        url = self.async_database_url
        # Replace the database name at the end of the URL
        return re.sub(r"/([^/?]+)(\?.*)?$", r"/\1_test\2", url)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
