from __future__ import annotations

from functools import lru_cache

from api.app.core.config import Settings as ApiSettings
from api.app.core.config import get_settings as get_api_settings


class Settings(ApiSettings):
    @property
    def app_env(self) -> str:
        return "production" if not self.debug else "development"

    @property
    def app_host(self) -> str:
        return self.api_host

    @property
    def app_port(self) -> int:
        return self.api_port


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    get_api_settings.cache_clear()
    return Settings.model_validate(get_api_settings().model_dump())
