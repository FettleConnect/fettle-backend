from __future__ import annotations

import os
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _setup_hospital_with_patient(
    async_client: AsyncClient, db_session: AsyncSession
) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.hospital.patient import Patient
    from api.app.models.organization import HospitalConfig, Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"S5 Hospital {uuid.uuid4().hex[:6]}",
        slug=f"s5-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    db_session.add(
        HospitalConfig(
            id=uuid.uuid4(),
            organization_id=org.id,
            vobiz_did="+919000000011",
            branding_json={},
        )
    )

    patient = Patient(
        id=uuid.uuid4(),
        organization_id=org.id,
        patient_name="Sprint5 Patient",
        mobile_no="+919500001111",
        department="Dermatology",
    )
    db_session.add(patient)

    email = f"s5_{uuid.uuid4().hex[:8]}@hospital.com"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password("TestPass123!"),
        name="S5 Admin",
        role="hospital_admin",
    )
    db_session.add(user)
    await db_session.flush()

    login = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "TestPass123!"}
    )
    token = login.json()["token"]
    return org.id, token, patient.id


# ── Agent Escalation Internal Endpoint ────────────────────────────────────────


class TestAgentEscalation:
    async def test_escalation_by_phone(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from sqlalchemy import select

        from api.app.models.hospital.call import Escalation

        _org_id, _, patient_id = await _setup_hospital_with_patient(
            async_client, db_session
        )
        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")

        resp = await async_client.post(
            "/api/internal/escalation/",
            headers={"X-Internal-API-Secret": secret},
            json={
                "phone_number": "+919500001111",
                "issue_description": "Patient needs urgent help",
                "department": "Dermatology",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0

        # Verify escalation was created
        row = await db_session.scalar(
            select(Escalation).where(
                Escalation.patient_id == patient_id,
                Escalation.issue_description == "Patient needs urgent help",
            )
        )
        assert row is not None

    async def test_escalation_by_patient_id(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _org_id, _, patient_id = await _setup_hospital_with_patient(
            async_client, db_session
        )
        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")

        resp = await async_client.post(
            "/api/internal/escalation/",
            headers={"X-Internal-API-Secret": secret},
            json={
                "patient_id": str(patient_id),
                "issue_description": "Escalation by ID",
                "department": "Cardiology",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["error"] == 0

    async def test_escalation_unknown_phone_returns_error(
        self, async_client: AsyncClient
    ) -> None:
        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
        resp = await async_client.post(
            "/api/internal/escalation/",
            headers={"X-Internal-API-Secret": secret},
            json={
                "phone_number": "+919999999999",
                "issue_description": "Not found",
            },
        )
        # Should fail gracefully — patient not found
        assert resp.status_code == 200
        assert resp.json()["error"] == 1

    async def test_escalation_requires_secret(self, async_client: AsyncClient) -> None:
        resp = await async_client.post(
            "/api/internal/escalation/",
            json={"phone_number": "+919500001111", "issue_description": "Test"},
        )
        assert resp.status_code in (401, 403)


# ── Dermatology Chatbot ───────────────────────────────────────────────────────


class TestDermatologyAuth:
    async def test_patient_auth_creates_patient(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, _, _ = await _setup_hospital_with_patient(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/chatbot/auth",
            json={
                "email": f"derm_{uuid.uuid4().hex[:8]}@patient.com",
                "name": "Derm Patient",
                "organization_id": str(org_id),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "token" in body
        assert "name" in body

    async def test_patient_auth_idempotent(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, _, _ = await _setup_hospital_with_patient(async_client, db_session)
        email = f"derm_{uuid.uuid4().hex[:8]}@patient.com"
        payload = {"email": email, "name": "Patient", "organization_id": str(org_id)}

        r1 = await async_client.post("/api/v1/chatbot/auth", json=payload)
        r2 = await async_client.post("/api/v1/chatbot/auth", json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Same patient returned both times
        assert r1.json()["email"] == r2.json()["email"]

    async def test_patient_auth_missing_email(self, async_client: AsyncClient) -> None:
        resp = await async_client.post(
            "/api/v1/chatbot/auth", json={"name": "No Email"}
        )
        assert resp.status_code == 422


class TestDermatologyChat:
    async def _auth(self, async_client: AsyncClient, db_session: AsyncSession) -> tuple:
        org_id, _, _ = await _setup_hospital_with_patient(async_client, db_session)
        email = f"chatuser_{uuid.uuid4().hex[:8]}@patient.com"
        resp = await async_client.post(
            "/api/v1/chatbot/auth",
            json={
                "email": email,
                "name": "Chat User",
                "organization_id": str(org_id),
            },
        )
        token = resp.json()["token"]
        return token, org_id

    async def test_chat_history_empty_for_new_patient(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token, _ = await self._auth(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/chatbot/chat",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "conv" in body
        assert "thread_id" in body
        assert "mode" in body
        assert body["mode"] == "general_education"

    async def test_chat_send_message(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token, _ = await self._auth(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/chatbot/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "What is eczema?"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body
        assert "mode" in body

    async def test_chat_requires_auth(self, async_client: AsyncClient) -> None:
        resp = await async_client.get("/api/v1/chatbot/chat")
        assert resp.status_code in (401, 403)

    async def test_consultation_list(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token, _ = await self._auth(async_client, db_session)
        # First chat creates a thread
        await async_client.get(
            "/api/v1/chatbot/chat", headers={"Authorization": f"Bearer {token}"}
        )
        resp = await async_client.get(
            "/api/v1/chatbot/consultations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "history" in body
        assert len(body["history"]) >= 1

    async def test_archive_consultation(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token, _ = await self._auth(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/chatbot/archive",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "thread_id" in body

    async def test_payment_confirm(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token, _ = await self._auth(async_client, db_session)
        # Get active thread
        hist = await async_client.get(
            "/api/v1/chatbot/chat", headers={"Authorization": f"Bearer {token}"}
        )
        thread_id = hist.json()["thread_id"]

        resp = await async_client.post(
            "/api/v1/chatbot/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "CONFIRM", "thread_id": str(thread_id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Mode should transition to post_payment_intake
        assert body["mode"] == "post_payment_intake"


class TestDermatologyDoctor:
    async def _setup_doctor(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> tuple:
        from api.app.core.security import hash_password
        from api.app.models.auth import User
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name=f"Derm Doctor Org {uuid.uuid4().hex[:6]}",
            slug=f"derm-doc-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        email = f"derm_doc_{uuid.uuid4().hex[:8]}@hospital.com"
        doctor = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=email,
            password_hash=hash_password("DocPass123!"),
            name="Dr. Derm",
            role="doctor",
            department="Dermatology",
        )
        db_session.add(doctor)
        await db_session.flush()

        login = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": "DocPass123!"}
        )
        token = login.json()["token"]
        return org.id, token, doctor.id

    async def test_doctor_list_consultations(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _org_id, token, _ = await self._setup_doctor(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/chatbot/doctor/consultations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "conversations" in body
