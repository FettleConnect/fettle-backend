from __future__ import annotations

import os

from celery import Celery


def _broker_url() -> str:
    return os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0"))


def _result_backend() -> str:
    return os.getenv("CELERY_RESULT_BACKEND", _broker_url())


celery_app = Celery("fettle")
celery_app.conf.update(
    broker_url=_broker_url(),
    result_backend=_result_backend(),
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.getenv("CELERY_TIMEZONE", "UTC"),
    enable_utc=True,
)

celery_app.autodiscover_tasks(["app.worker"])
