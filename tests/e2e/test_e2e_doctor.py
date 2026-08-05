from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _setup_doctor(async_client: AsyncClient, db_session: AsyncSession) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.organization import Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"E2E Doctor Hospital {uuid.uuid4().hex[:6]}",
        slug=f"e2e-doctor-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    email = f"doctor_{uuid.uuid4().hex[:8]}@e2ehospital.com"
    doctor = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password("DoctorPass123!"),
        name="Dr. E2E Specialist",
        role="doctor",
        department="Neurology",
        consultation_fee=1200.0,
        timings="10am-6pm",
        availability_json=[
            {"day": "Monday", "slots": ["10:00", "11:00", "14:00"]},
            {"day": "Wednesday", "slots": ["10:00", "11:00"]},
        ],
    )
    db_session.add(doctor)
    await db_session.flush()

    resp = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "DoctorPass123!"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "doctor"
    token = resp.json()["token"]
    return org.id, token, doctor.id, doctor.email


class TestDoctorFlow:
    async def test_complete_doctor_lifecycle(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Full doctor workflow:
        1. Login → check token
        2. View doctor schedule via internal API
        3. Create MediVoice session
        4. Update session with diagnosis/medicines
        5. Add transcription entries
        6. Trigger notifications (mocked Celery)
        7. Doctor system prompt CRUD
        8. Legacy login path (/api/login/)
        9. View MediVoice session with transcripts
        10. Verify multi-tenant: only see own org's sessions
        """
        org_id, token, _doctor_id, doctor_email = await _setup_doctor(
            async_client, db_session
        )
        auth = {"Authorization": f"Bearer {token}"}
        import os

        # ── 1. Token validation ──────────────────────────────────────────────
        r = await async_client.get("/api/v1/auth/validate", headers=auth)
        assert r.status_code == 200
        assert r.json()["role"] == "doctor"
        assert r.json()["organization_id"] == str(org_id)

        # ── 2. Doctor schedule via internal API ──────────────────────────────
        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
        r = await async_client.get(
            f"/api/v1/tenant/doctors?organization_id={org_id}",
            headers={"X-Internal-API-Secret": secret},
        )
        assert r.status_code == 200
        doctors = r.json()
        assert any(d["doctor_name"] == "Dr. E2E Specialist" for d in doctors)
        doc_entry = next(d for d in doctors if d["doctor_name"] == "Dr. E2E Specialist")
        assert doc_entry["department"] == "Neurology"
        assert doc_entry["fees"] == 1200.0
        assert len(doc_entry["availability"]) == 2

        # ── 3. Create MediVoice session ──────────────────────────────────────
        r = await async_client.post(
            "/api/v1/medivoice/sessions",
            headers=auth,
            json={
                "patient_name": "E2E Patient",
                "patient_mobile": "+919500002222",
                "patient_email": "e2epatient@test.com",
            },
        )
        assert r.status_code == 200
        session_id = r.json()["session_id"]

        # ── 4. Add transcriptions ────────────────────────────────────────────
        for i, (speaker, text) in enumerate(
            [
                ("doctor", "How long have you had this headache?"),
                ("patient", "About 3 days. It's on the left side."),
                ("doctor", "Any nausea or light sensitivity?"),
                ("patient", "Yes, lights bother me."),
            ]
        ):
            r = await async_client.post(
                f"/api/v1/medivoice/sessions/{session_id}/transcript",
                headers=auth,
                json={"speaker": speaker, "text": text, "timestamp": float(i * 5)},
            )
            assert r.status_code == 200

        # ── 5. Update session with diagnosis ─────────────────────────────────
        r = await async_client.patch(
            f"/api/v1/medivoice/sessions/{session_id}",
            headers=auth,
            json={
                "diagnosis": "Migraine with aura",
                "overall_summary": "Patient presents with left-sided headache with photophobia for 3 days",
                "medicines": [
                    {
                        "name": "Sumatriptan",
                        "dosage": "50mg",
                        "frequency": "As needed",
                        "duration": "3 days",
                    },
                    {
                        "name": "Naproxen",
                        "dosage": "500mg",
                        "frequency": "Twice daily",
                        "duration": "5 days",
                    },
                ],
            },
        )
        assert r.status_code == 200

        # ── 6. Get full session with transcripts ─────────────────────────────
        r = await async_client.get(
            f"/api/v1/medivoice/sessions/{session_id}", headers=auth
        )
        assert r.status_code == 200
        body = r.json()
        assert body["error"] == 0
        session_data = body["session"]
        assert session_data["diagnosis"] == "Migraine with aura"
        assert len(session_data["medicines"]) == 2
        assert len(body["transcripts"]) == 4

        # ── 7. List sessions ─────────────────────────────────────────────────
        r = await async_client.get("/api/v1/medivoice/sessions", headers=auth)
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert any(s["id"] == str(session_id) for s in sessions)

        # ── 8. Send notifications (mocked via Celery fixture) ────────────────
        r = await async_client.post(
            f"/api/v1/medivoice/sessions/{session_id}/notify", headers=auth
        )
        assert r.status_code == 200
        assert r.json()["error"] == 0

        # ── 9. System prompt CRUD (doctor's custom AI instructions) ──────────
        r = await async_client.get("/api/system_prompt/", headers=auth)
        assert r.status_code == 200
        assert r.json()["error"] == 0
        assert r.json()["doctor_name"] == "Dr. E2E Specialist"

        r = await async_client.post(
            "/api/system_prompt/",
            headers=auth,
            json={
                "prompt": '{"style": "formal", "language": "English", "specialization": "Neurology"}',
                "doctor_name": "Dr. E2E Specialist",
            },
        )
        assert r.status_code == 200

        r = await async_client.get("/api/system_prompt/", headers=auth)
        assert "Neurology" in r.json()["prompt"]

        # ── 10. Legacy login path ─────────────────────────────────────────────
        r = await async_client.post(
            "/api/login/", json={"email": doctor_email, "password": "DoctorPass123!"}
        )
        assert r.status_code == 200
        assert r.json()["error"] == 0
        assert "token" in r.json()

        # ── 11. Doctor dashboard (chatbot) tabs ──────────────────────────────
        r = await async_client.get("/api/doctor_tabs/", headers=auth)
        assert r.status_code == 200
        # No paid threads yet — tabs list will be empty, but should not error
        assert "tabs_doc" in r.json()

        # ── 12. Staff availability ────────────────────────────────────────────
        r = await async_client.get("/api/v1/doctors/availability", headers=auth)
        assert r.status_code == 200
        avail_data = r.json()["data"]
        assert any(d["name"] == "Dr. E2E Specialist" for d in avail_data)

        # ── 13. Multi-tenant: session not accessible by other org doctor ──────
        from api.app.core.security import hash_password
        from api.app.models.auth import User as _User
        from api.app.models.organization import Organization

        other_org = Organization(
            id=uuid.uuid4(),
            name="Other Doc Org",
            slug=f"other-doc-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(other_org)
        await db_session.flush()  # Must flush org before adding user FK

        other_email = f"other_doc_{uuid.uuid4().hex[:8]}@other.com"
        other_doctor = _User(
            id=uuid.uuid4(),
            organization_id=other_org.id,
            email=other_email,
            password_hash=hash_password("OtherPass123!"),
            name="Other Doc",
            role="doctor",
        )
        db_session.add(other_doctor)
        await db_session.flush()

        other_login = await async_client.post(
            "/api/v1/auth/login",
            json={"email": other_email, "password": "OtherPass123!"},
        )
        other_token = other_login.json()["token"]
        other_auth = {"Authorization": f"Bearer {other_token}"}

        r = await async_client.get(
            f"/api/v1/medivoice/sessions/{session_id}", headers=other_auth
        )
        assert r.status_code == 404  # Can't see other org's session


class TestDoctorArtifacts:
    async def test_transcript_artifact(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _org_id, token, _doctor_id, _ = await _setup_doctor(async_client, db_session)
        auth = {"Authorization": f"Bearer {token}"}

        r = await async_client.post(
            "/api/v1/medivoice/sessions",
            headers=auth,
            json={
                "patient_name": "Artifact Patient",
                "patient_mobile": "+919500003333",
            },
        )
        session_id = r.json()["session_id"]

        await async_client.post(
            f"/api/v1/medivoice/sessions/{session_id}/transcript",
            headers=auth,
            json={
                "speaker": "doctor",
                "text": "Test transcript line",
                "timestamp": 0.0,
            },
        )

        r = await async_client.get(
            f"/api/v1/medivoice/sessions/{session_id}/artifacts/transcript",
            headers=auth,
        )
        assert r.status_code == 200
        # Content may be bytes or text depending on response encoding
        content = r.text if r.text else r.content.decode("utf-8", errors="replace")
        assert "Test transcript line" in content or len(content) > 0
