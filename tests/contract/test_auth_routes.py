from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fettle_backend.app.config import get_settings
from fettle_backend.app.main import create_app


@pytest.fixture
def internal_admin_client(client, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_API_ENABLED", "true")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    get_settings.cache_clear()
    with TestClient(create_app()) as admin_client:
        yield admin_client
    get_settings.cache_clear()


def test_list_doctors_returns_seeded_profiles(client) -> None:
    response = client.get("/doctors")

    assert response.status_code == 200
    doctors = response.json()["doctors"]
    assert len(doctors) >= 3
    assert "name" in doctors[0]


def test_auth_select_returns_pin_login_required(client) -> None:
    doctors = client.get("/doctors").json()["doctors"]
    response = client.post("/auth/select", json={"doctorId": doctors[0]["id"]})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PIN_LOGIN_REQUIRED"


def test_pin_login_and_auth_me_flow(client) -> None:
    doctors = client.get("/doctors").json()["doctors"]
    for doctor in doctors:
        login = client.post(
            "/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"}
        )
        if login.json().get("status") == "authenticated":
            break

    assert login.status_code == 200
    payload = login.json()
    assert payload["status"] == "authenticated"
    token = payload["token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["doctor"]["id"] == payload["doctor"]["id"]


def test_pin_change_flow_returns_authenticated_session(internal_admin_client) -> None:
    created = internal_admin_client.post(
        "/internal/admin/doctors",
        headers={"X-Internal-API-Key": "test-internal-key"},
        json={
            "name": "Dr. Pin Change",
            "department": "General",
            "phoneNumber": "9000000001",
        },
    )
    doctor = created.json()["doctor"]

    reset = internal_admin_client.post(
        f"/internal/admin/doctors/{doctor['id']}/pin-reset",
        headers={"X-Internal-API-Key": "test-internal-key"},
    )
    temporary_pin = reset.json()["temporaryPin"]

    login = internal_admin_client.post(
        "/auth/pin/login", json={"doctorId": doctor["id"], "pin": temporary_pin}
    )
    assert login.status_code == 200
    assert login.json()["status"] == "pin_change_required"

    changed = internal_admin_client.post(
        "/auth/pin/change",
        json={"pinChangeToken": login.json()["pinChangeToken"], "newPin": "123456"},
    )
    assert changed.status_code == 200
    payload = changed.json()
    assert payload["token"]
    assert payload["doctor"]["id"] == doctor["id"]

    me = internal_admin_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {payload['token']}"}
    )
    assert me.status_code == 200
    assert me.json()["doctor"]["id"] == doctor["id"]

    relogin = internal_admin_client.post(
        "/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"}
    )
    assert relogin.status_code == 200
    assert relogin.json()["status"] == "authenticated"


def test_pin_change_rejects_forged_token(client) -> None:
    doctor = client.get("/doctors").json()["doctors"][0]
    response = client.post(
        "/auth/pin/change",
        json={"pinChangeToken": f"doctor:{doctor['id']}", "newPin": "123456"},
    )
    assert response.status_code == 401


def test_pin_reset_revokes_existing_session(internal_admin_client) -> None:
    doctors = internal_admin_client.get("/doctors").json()["doctors"]
    for doctor in doctors:
        login = internal_admin_client.post(
            "/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"}
        )
        if login.json().get("status") == "authenticated":
            break
    token = login.json()["token"]

    reset = internal_admin_client.post(
        f"/internal/admin/doctors/{doctor['id']}/pin-reset",
        headers={"X-Internal-API-Key": "test-internal-key"},
    )
    assert reset.status_code == 200

    me = internal_admin_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 404
