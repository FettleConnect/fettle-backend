from __future__ import annotations

import os
from typing import Any, cast

from redis import Redis

OUTBOUND_CALLS_COUNT_KEY = "active_outbound_calls_count"
CONCURRENCY_LIMIT = int(os.getenv("OUTBOUND_CONCURRENCY_LIMIT", "3"))


def _redis_url() -> str:
    return os.getenv(
        "REDIS_URL", os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    )


def get_redis_client() -> Redis:
    return cast(Redis, Redis.from_url(_redis_url(), decode_responses=True))


def get_active_outbound_calls_count() -> int:
    client = get_redis_client()
    try:
        value = client.get(OUTBOUND_CALLS_COUNT_KEY)
        return int(cast(Any, value) or 0)
    finally:
        client.close()


def increment_active_outbound_calls() -> int:
    client = get_redis_client()
    try:
        return int(cast(Any, client.incr(OUTBOUND_CALLS_COUNT_KEY)))
    finally:
        client.close()


def decrement_active_outbound_calls() -> int:
    client = get_redis_client()
    try:
        value = int(cast(Any, client.decr(OUTBOUND_CALLS_COUNT_KEY)))
        if value < 0:
            client.set(OUTBOUND_CALLS_COUNT_KEY, 0)
            return 0
        return value
    finally:
        client.close()
