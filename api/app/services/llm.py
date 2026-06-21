from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from api.app.core.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def openrouter_api_key() -> str:
    """Return the OpenRouter API key, with OPENAI_API_KEY kept as a legacy alias."""
    return settings.openrouter_api_key or settings.openai_api_key


def openrouter_model() -> str:
    return settings.openrouter_model


@lru_cache
def llm_client() -> OpenAI:
    return OpenAI(api_key=openrouter_api_key(), base_url=OPENROUTER_BASE_URL)
