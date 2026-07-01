from __future__ import annotations


def test_healthcheck(sync_client) -> None:
    response = sync_client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
