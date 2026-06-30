from __future__ import annotations


def test_migration_status_lists_completed_surface(client) -> None:
    response = client.get("/internal/migration-status")

    assert response.status_code == 200
    body = response.json()
    assert body["product"] == "transcribe"
    assert "fastapi-app" in body["implemented"]
    assert "auth-session-compatibility-skeleton" in body["implemented"]
    assert "report-context-routes" in body["implemented"]
    assert "admin-routes" in body["implemented"]
    assert "kpi-reliability-routes" in body["implemented"]
    assert "pdf-delivery-routes" in body["implemented"]
    assert body["pending"] == []
