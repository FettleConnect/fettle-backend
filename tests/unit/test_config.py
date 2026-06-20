from __future__ import annotations

import os

import pytest


class TestSettings:
    def test_settings_loads(self) -> None:
        from api.app.core.config import settings

        assert settings.app_name == "Fettle API"
        assert settings.jwt_algorithm == "HS256"
        assert settings.jwt_access_token_expire_minutes > 0

    def test_jwt_secret_at_least_32_chars(self) -> None:
        from api.app.core.config import settings

        assert len(settings.jwt_secret_key) >= 32

    def test_jwt_secret_rejects_short_value(self) -> None:
        from pydantic import ValidationError
        from pydantic_settings import BaseSettings

        from api.app.core.config import Settings

        with pytest.raises((ValidationError, ValueError)):
            Settings(jwt_secret_key="short", database_url="postgresql+psycopg://x/y")  # type: ignore[call-arg]

    def test_async_database_url_uses_psycopg_async(self) -> None:
        from api.app.core.config import settings

        assert "psycopg_async" in settings.async_database_url

    def test_sync_database_url_uses_psycopg(self) -> None:
        from api.app.core.config import settings

        assert "psycopg_async" not in settings.sync_database_url
        assert (
            "psycopg" in settings.sync_database_url
            or "postgresql" in settings.sync_database_url
        )

    def test_test_database_url_has_test_suffix(self) -> None:
        from api.app.core.config import settings

        assert (
            "_test" in settings.test_database_url
            or "test" in settings.test_database_url
        )
