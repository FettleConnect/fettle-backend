from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fettle_backend.app.config import get_settings
from fettle_backend.app.main import create_app


@pytest.fixture
def internal_health_client(client, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_API_ENABLED", "true")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_healthcheck(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "api"
    assert payload["version"] == "0.1.0"
    assert payload["timestamp"]
    assert payload["mockMode"] is True


def test_internal_health_requires_internal_auth(client) -> None:
    response = client.get("/internal/health")

    assert response.status_code == 401


def test_internal_healthcheck(internal_health_client) -> None:
    response = internal_health_client.get(
        "/internal/health",
        headers={
            "X-Internal-API-Key": "test-internal-key",
            "X-Internal-Caller": "standup-check",
            "X-Admin-User-Id": "admin-1",
            "X-Admin-User-Email": "admin@example.com",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "api"
    assert payload["internalApi"] == {
        "authenticated": True,
        "authenticationMethod": "x-internal-api-key",
        "caller": "standup-check",
        "adminUserId": "admin-1",
        "adminUserEmail": "admin@example.com",
    }
