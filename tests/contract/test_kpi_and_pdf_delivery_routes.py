from __future__ import annotations

import asyncio
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from api.app.core.config import get_settings
from api.app.main import app
from api.app.transcribe.db import SessionFactory
from api.app.transcribe.observability import ObservabilityService
from api.app.transcribe.storage import LocalStorage
from api.app.transcribe.repositories.sqlalchemy import (
    SqlSessionRepository,
)
from api.app.transcribe.services.pdf_delivery import PdfDeliveryService


def _auth_headers(client):
    for doctor in client.get("/doctors").json()["doctors"]:
        login = client.post(
            "/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"}
        )
        token = login.json().get("token")
        if token:
            return {"Authorization": f"Bearer {token}"}
    raise AssertionError("no doctor could authenticate with the seeded PIN")


async def _seed_kpi_events(events: list[tuple[str, str, dict]]) -> None:
    async with SessionFactory() as session:
        service = ObservabilityService(session)
        for metric_key, status, payload in events:
            await service.record_event(metric_key, status, payload)


async def _process_pdf_delivery_jobs() -> list[dict]:
    async with SessionFactory() as session:
        service = PdfDeliveryService(
            SqlSessionRepository(session), get_settings(), LocalStorage()
        )
        return await service.process_pending_jobs()


async def _create_public_pdf_media_url(session_id: str, file_name: str) -> str:
    async with SessionFactory() as session:
        service = PdfDeliveryService(
            SqlSessionRepository(session), get_settings(), LocalStorage()
        )
        return service.create_public_media_url(session_id, file_name)


@pytest.fixture
def internal_admin_client(client, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_API_ENABLED", "true")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    get_settings.cache_clear()
    with TestClient(app) as admin_client:
        yield admin_client
    get_settings.cache_clear()


@pytest.fixture
def fettle_sync_admin_client(client, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_API_ENABLED", "true")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    monkeypatch.setenv("FETTLE_SYNC_ENABLED", "true")
    monkeypatch.setenv("FETTLE_BACKEND_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("FETTLE_SYNC_SECRET", "test-secret")
    get_settings.cache_clear()
    with TestClient(app) as admin_client:
        yield admin_client
    get_settings.cache_clear()


def _admin_headers() -> dict[str, str]:
    return {"X-Internal-API-Key": "test-internal-key"}


def test_public_kpi_scorecard_and_filters(client) -> None:
    asyncio.run(
        _seed_kpi_events(
            [
                ("auth.pin_login", "success", {"elapsedMs": 120}),
                ("auth.pin_login", "failure", {"elapsedMs": 80}),
                ("session.audio_upload", "success", {"elapsedMs": 300}),
            ]
        )
    )

    scorecard = client.get("/public/kpi/scorecard")
    assert scorecard.status_code == 200
    payload = scorecard.json()
    assert payload["generatedAt"]
    assert any(section["section"] == "auth" for section in payload["sections"])
    assert any(
        metric["metric"] == "auth.pin_login"
        for section in payload["sections"]
        for metric in section["metrics"]
    )

    focus = client.get("/public/kpi/scorecard?focus=1")
    assert focus.status_code == 200
    assert focus.json()["sections"][0]["section"] == "auth"

    filtered = client.get("/public/kpi/scorecard?includeExpected=1&section=session")
    assert filtered.status_code == 200
    assert filtered.json()["sections"][0]["section"] == "session"
    assert any(
        metric["metric"] == "session.transcription.end_to_end"
        for metric in filtered.json()["sections"][0]["metrics"]
    )


def test_public_kpi_diagnostics_routes(client) -> None:
    asyncio.run(
        _seed_kpi_events(
            [
                (
                    "provider.gemini.health",
                    "success",
                    {"provider": "gemini-2.5-flash", "elapsedMs": 210},
                ),
                (
                    "provider.gemini.health",
                    "failure",
                    {
                        "provider": "gemini-2.5-flash",
                        "elapsedMs": 900,
                        "error": "Gemini transcription failed with status 503.",
                    },
                ),
                ("session.transcription.queue", "success", {"elapsedMs": 50}),
                (
                    "session.transcription.provider_call",
                    "failure",
                    {
                        "elapsedMs": 1800,
                        "error": "OpenAI transcription failed with status 500.",
                    },
                ),
                (
                    "report_context.preprocess",
                    "success",
                    {"elapsedMs": 120, "unitCount": 3},
                ),
                (
                    "report_context.fallback_path",
                    "success",
                    {
                        "provider": "gemini-2.5-flash-report-context",
                        "operation": "extract_chunks",
                        "fallbackUsed": False,
                    },
                ),
                (
                    "report_context.fallback_path",
                    "failure",
                    {
                        "provider": "gpt-5.4-mini-report-context",
                        "operation": "suggest_draft",
                        "fallbackUsed": True,
                        "error": "REPORT_CONTEXT_PROVIDER_TIMEOUT",
                    },
                ),
            ]
        )
    )

    providers = client.get("/public/kpi/providers?window=today")
    assert providers.status_code == 200
    assert providers.json()["providers"][0]["provider"] == "gemini-2.5-flash"

    pipeline = client.get("/public/kpi/pipeline?window=today")
    assert pipeline.status_code == 200
    assert any(
        stage["metric"] == "report_context.extract_parse"
        for stage in pipeline.json()["stages"]
    )

    fallback = client.get("/public/kpi/report-context-fallback?window=today")
    assert fallback.status_code == 200
    assert fallback.json()["summary"]["fallbackUsed"] >= 1

    trends = client.get("/public/kpi/event-trends?window=today")
    assert trends.status_code == 200
    assert trends.json()["points"][0]["totalEvents"] >= 1


def test_internal_reliability_and_pdf_delivery_disabled_routes(
    internal_admin_client,
) -> None:
    asyncio.run(
        _seed_kpi_events(
            [("session.transcription.end_to_end", "failure", {"elapsedMs": 1500})]
        )
    )

    alerts = internal_admin_client.get(
        "/internal/admin/reliability/alerts", headers=_admin_headers()
    )
    assert alerts.status_code == 200
    assert any(
        alert["metric"] == "session.transcription.end_to_end"
        for alert in alerts.json()["alerts"]
    )

    sync_dry_run = internal_admin_client.post(
        "/internal/admin/fettle-sync/dry-run", headers=_admin_headers()
    )
    assert sync_dry_run.status_code == 409
    assert "Fettle sync is not configured" in sync_dry_run.json()["error"]

    pdf_job = internal_admin_client.get(
        "/internal/admin/pdf-delivery/jobs/123", headers=_admin_headers()
    )
    assert pdf_job.status_code == 404
    assert "PDF delivery job was not found" in pdf_job.json()["error"]


def test_internal_fettle_sync_enabled_dry_run_and_session_enqueue(
    fettle_sync_admin_client,
) -> None:
    dry_run = fettle_sync_admin_client.post(
        "/internal/admin/fettle-sync/dry-run", headers=_admin_headers()
    )
    assert dry_run.status_code == 202
    assert dry_run.json()["job"]["trigger"] == "dry_run"
    assert dry_run.json()["result"] == {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "deadLetter": 0,
    }

    replay_missing = fettle_sync_admin_client.post(
        "/internal/admin/fettle-sync/jobs/99999/replay", headers=_admin_headers()
    )
    assert replay_missing.status_code == 404
    assert "Dead-letter sync job was not found" in replay_missing.json()["error"]

    headers = _auth_headers(fettle_sync_admin_client)
    session = fettle_sync_admin_client.post(
        "/sessions",
        json={
            "patientName": "Kiran Rao",
            "patientPhoneNumber": "9000000001",
            "patientAge": 37,
        },
        headers=headers,
    ).json()["session"]
    fettle_sync_admin_client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "12",
            "X-Audio-File-Name": "sync.webm",
        },
        content=b"sync-audio",
    )
    fettle_sync_admin_client.post(
        f"/sessions/{session['id']}/transcription",
        headers={**headers, "X-Idempotency-Key": f"sync-{session['id']}"},
    )
    import time

    for _ in range(10):
        current = fettle_sync_admin_client.get(
            f"/sessions/{session['id']}", headers=headers
        ).json()["session"]
        if current["transcription"]["status"] == "completed":
            break
        time.sleep(0.05)

    queued_jobs = fettle_sync_admin_client.get(
        "/internal/admin/fettle-sync/jobs?status=queued", headers=_admin_headers()
    )
    assert queued_jobs.status_code == 200
    assert any(
        job["sessionId"] == session["id"]
        and job["trigger"] == "transcription_completed"
        for job in queued_jobs.json()["jobs"]
    )

    stats = fettle_sync_admin_client.get(
        "/internal/admin/fettle-sync/stats", headers=_admin_headers()
    )
    assert stats.status_code == 200
    assert stats.json()["enabled"] is True
    assert stats.json()["succeeded"] >= 1
    assert stats.json()["queued"] >= 1


def test_session_pdf_delivery_settings_jobs_webhook_and_public_media(
    client, internal_admin_client
) -> None:
    headers = _auth_headers(client)
    session = client.post(
        "/sessions",
        json={
            "patientName": "Anita Sharma",
            "patientPhoneNumber": "9876543210",
            "patientAge": 32,
        },
        headers=headers,
    ).json()["session"]

    default_delivery = client.get(
        f"/sessions/{session['id']}/pdf-delivery", headers=headers
    )
    assert default_delivery.status_code == 200
    assert default_delivery.json()["delivery"]["settings"]["patientEnabled"] is True
    assert default_delivery.json()["delivery"]["settings"]["pharmacyEnabled"] is False

    send_without_pdf = client.post(
        f"/sessions/{session['id']}/pdf-delivery/send",
        headers=headers,
        json={"patientEnabled": True, "pharmacyEnabled": False},
    )
    assert send_without_pdf.status_code == 409

    prepared = client.put(
        f"/sessions/{session['id']}/pdf-delivery",
        headers=headers,
        json={
            "patientEnabled": True,
            "pharmacyEnabled": True,
            "pharmacy": {"name": "CarePlus Pharmacy", "phoneNumber": "9988776655"},
        },
    )
    assert prepared.status_code == 200
    assert prepared.json()["delivery"]["settings"]["pharmacyEnabled"] is True

    client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "10",
            "X-Audio-File-Name": "delivery.webm",
        },
        content=b"delivery-audio",
    )
    client.post(
        f"/sessions/{session['id']}/transcription",
        headers={**headers, "X-Idempotency-Key": f"delivery-{session['id']}"},
    )
    import time

    for _ in range(10):
        current = client.get(f"/sessions/{session['id']}", headers=headers).json()[
            "session"
        ]
        if current["transcription"]["status"] == "completed":
            break
        time.sleep(0.05)
    generated = client.post(f"/sessions/{session['id']}/pdf", headers=headers)
    assert generated.status_code == 200
    artifact = generated.json()["session"]["documentArtifact"]

    status = client.get(f"/sessions/{session['id']}/pdf-delivery", headers=headers)
    assert status.status_code == 200
    jobs = status.json()["delivery"]["jobs"]
    assert sorted(job["recipientType"] for job in jobs) == ["patient", "pharmacy"]
    assert all(job["status"] == "pending" for job in jobs)

    admin_jobs = internal_admin_client.get(
        f"/internal/admin/pdf-delivery/jobs?sessionId={session['id']}",
        headers=_admin_headers(),
    )
    assert admin_jobs.status_code == 200
    assert len(admin_jobs.json()["jobs"]) == 2

    processed = asyncio.run(_process_pdf_delivery_jobs())
    assert len(processed) == 2
    assert all(job["status"] == "queued" for job in processed)
    provider_message_id = processed[0]["providerMessageId"]

    webhook = client.post(
        "/webhooks/twilio/whatsapp-status",
        data={"MessageSid": provider_message_id, "MessageStatus": "delivered"},
    )
    assert webhook.status_code == 204

    job_detail = internal_admin_client.get(
        f"/internal/admin/pdf-delivery/jobs/{processed[0]['id']}",
        headers=_admin_headers(),
    )
    assert job_detail.status_code == 200
    assert job_detail.json()["job"]["status"] == "delivered"

    media_url = asyncio.run(
        _create_public_pdf_media_url(session["id"], artifact["fileName"])
    )
    parsed_media_url = urlparse(media_url)
    token = parse_qs(parsed_media_url.query)["token"][0]

    media = client.get(f"{parsed_media_url.path}?token={token}")
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("application/pdf")
    assert media.content.startswith(b"%PDF")

    invalid_media = client.get("/public/pdf-delivery/media?token=invalid-token")
    assert invalid_media.status_code == 404

    sample = client.get("/public/pdf-delivery/template-sample.pdf")
    assert sample.status_code == 200
    assert sample.headers["content-type"].startswith("application/pdf")
    assert "medivoice-template-sample.pdf" in sample.headers["content-disposition"]
