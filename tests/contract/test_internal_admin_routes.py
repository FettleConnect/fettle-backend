from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fettle_backend.app.config import get_settings
from fettle_backend.app.main import create_app


def _auth_headers(client):
    for doctor in client.get("/doctors").json()["doctors"]:
        login = client.post("/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"})
        body = login.json()
        token = body.get("token")
        if token:
            return {"Authorization": f"Bearer {token}"}
    raise AssertionError("no doctor could authenticate with the seeded PIN")


@pytest.fixture
def internal_admin_client(client, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_API_ENABLED", "true")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    get_settings.cache_clear()
    with TestClient(create_app()) as admin_client:
        yield admin_client
    get_settings.cache_clear()


def _admin_headers() -> dict[str, str]:
    return {"X-Internal-API-Key": "test-internal-key"}


def test_internal_admin_doctor_create_list_and_detail(internal_admin_client) -> None:
    name = "Dr. Admin Created 001"
    created = internal_admin_client.post(
        "/internal/admin/doctors",
        headers=_admin_headers(),
        json={"name": name, "department": "Cardiology", "phoneNumber": "9123456789"},
    )
    assert created.status_code == 201
    doctor = created.json()["doctor"]

    listed = internal_admin_client.get("/internal/admin/doctors", headers=_admin_headers())
    assert listed.status_code == 200
    assert any(item["id"] == doctor["id"] for item in listed.json()["doctors"])

    detail = internal_admin_client.get(f"/internal/admin/doctors/{doctor['id']}", headers=_admin_headers())
    assert detail.status_code == 200
    assert detail.json()["doctor"]["name"] == name
    assert "pinHash" not in detail.json()["doctor"]
    assert "tokenVersion" not in detail.json()["doctor"]


def test_internal_admin_queue_listing_and_acceptance(internal_admin_client) -> None:
    doctor_headers = _auth_headers(internal_admin_client)
    intake_link = internal_admin_client.get("/intake-link", headers=doctor_headers).json()["intakeLink"]
    created = internal_admin_client.post(
        f"/public/intake/{intake_link['token']}",
        json={"patient": {"name": "Ravi Kumar", "phoneNumber": "9988776655", "age": 41}},
    ).json()["entry"]

    listed = internal_admin_client.get("/internal/admin/queue", headers=_admin_headers())
    assert listed.status_code == 200
    assert any(item["id"] == created["id"] for item in listed.json()["entries"])

    accepted = internal_admin_client.post(
        f"/internal/admin/queue/{created['id']}/accept",
        headers=_admin_headers(),
    )
    assert accepted.status_code == 201
    assert accepted.json()["entry"]["status"] == "accepted"


def test_internal_admin_session_artifacts_and_sync_disabled_contract(internal_admin_client) -> None:
    headers = _auth_headers(internal_admin_client)
    session = internal_admin_client.post(
        "/sessions",
        json={"patientName": "Anita Sharma", "patientPhoneNumber": "9876543210", "patientAge": 32},
        headers=headers,
    ).json()["session"]
    internal_admin_client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "10",
            "X-Audio-File-Name": "consultation.webm",
        },
        content=b"audio",
    )
    internal_admin_client.post(
        f"/sessions/{session['id']}/transcription",
        headers={**headers, "X-Idempotency-Key": "admin-session"},
    )
    for _ in range(10):
        time.sleep(0.05)
        current = internal_admin_client.get(f"/sessions/{session['id']}", headers=headers).json()["session"]
        if current["transcription"]["status"] == "completed":
            break
    internal_admin_client.post(f"/sessions/{session['id']}/pdf", headers=headers)

    transcription = internal_admin_client.get(
        f"/internal/admin/sessions/{session['id']}/transcription",
        headers=_admin_headers(),
    )
    assert transcription.status_code == 200
    assert transcription.json()["transcription"]["status"] == "completed"

    pdf = internal_admin_client.get(
        f"/internal/admin/sessions/{session['id']}/artifacts/pdf",
        headers=_admin_headers(),
    )
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")

    stats = internal_admin_client.get("/internal/admin/fettle-sync/stats", headers=_admin_headers())
    assert stats.status_code == 200
    assert stats.json()["enabled"] is False

    jobs = internal_admin_client.get("/internal/admin/fettle-sync/jobs", headers=_admin_headers())
    assert jobs.status_code == 200
    assert jobs.json()["jobs"] == []


def test_internal_admin_session_mutation_routes(internal_admin_client) -> None:
    doctor = internal_admin_client.get("/doctors").json()["doctors"][0]
    created = internal_admin_client.post(
        "/internal/admin/sessions",
        headers=_admin_headers(),
        json={
            "doctorId": doctor["id"],
            "patient": {"name": "Meera Iyer", "phoneNumber": "9012345678", "age": 29},
        },
    )
    assert created.status_code == 201
    session = created.json()["session"]

    listed = internal_admin_client.get("/internal/admin/sessions", headers=_admin_headers())
    assert listed.status_code == 200
    assert any(item["id"] == session["id"] for item in listed.json()["sessions"])

    detail = internal_admin_client.get(f"/internal/admin/sessions/{session['id']}", headers=_admin_headers())
    assert detail.status_code == 200
    assert detail.json()["session"]["patient"]["name"] == "Meera Iyer"

    doctor_headers = _auth_headers(internal_admin_client)
    audio = internal_admin_client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **doctor_headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "8",
            "X-Audio-File-Name": "admin-flow.webm",
        },
        content=b"admin-audio",
    )
    assert audio.status_code == 201

    transcription = internal_admin_client.post(
        f"/internal/admin/sessions/{session['id']}/transcription",
        headers={**_admin_headers(), "X-Idempotency-Key": "admin-mutation"},
    )
    assert transcription.status_code == 202

    current = None
    for _ in range(10):
        time.sleep(0.05)
        current = internal_admin_client.get(f"/internal/admin/sessions/{session['id']}", headers=_admin_headers())
        if current.json()["session"]["transcription"]["status"] == "completed":
            break

    assert current is not None
    assert current.status_code == 200
    assert current.json()["session"]["transcription"]["status"] == "completed"

    updated = internal_admin_client.put(
        f"/internal/admin/sessions/{session['id']}/draft-document",
        headers=_admin_headers(),
        json={
            "title": "Admin Draft",
            "clinicalSummary": "Admin-updated draft",
            "nextSteps": ["Share with patient"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["session"]["transcription"]["draftDocument"]["title"] == "Admin Draft"

    pdf = internal_admin_client.post(f"/internal/admin/sessions/{session['id']}/pdf", headers=_admin_headers())
    assert pdf.status_code == 200
    assert pdf.json()["session"]["documentArtifact"] is not None

    filtered = internal_admin_client.get(
        "/internal/admin/sessions?transcriptionStatus=completed&hasDocument=true",
        headers=_admin_headers(),
    )
    assert filtered.status_code == 200
    assert any(item["id"] == session["id"] for item in filtered.json()["sessions"])
