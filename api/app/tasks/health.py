from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(name="app.tasks.health.ping")
def ping() -> dict[str, str]:
    return {"status": "ok", "task": "app.tasks.health.ping"}
