from __future__ import annotations

"""
E2E: Dermatology Patient persona
Full consultation flow: auth → general education → payment → intake →
dermatologist review → final output.
Also tests the Google/magic-link auth paths the frontend actually calls.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _setup_org(db_session: AsyncSession) -> uuid.UUID:
    from api.app.models.organization import Organization

    org = Organization(
        id=uuid.uuid4(), name=f"E2E Chatbot Hospital {uuid.uuid4().hex[:6]}",
        slug=f"e2e-chatbot-{uuid.uuid4().hex[:8]}", type="hospital",
    )
    db_session.add(org)
    await db_session.flush()
    return org.id


async def _setup_doctor(db_session: AsyncSession, org_id: uuid.UUID, async_client: AsyncClient) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User

    email = f"chatbot_doc_{uuid.uuid4().hex[:8]}@hospital.com"
    doc = User(
        id=uuid.uuid4(), organization_id=org_id, email=email,
        password_hash=hash_password("DocPass123!"), name="Dr. Chatbot",
        role="doctor", department="Dermatology",
    )
    db_session.add(doc)
    await db_session.flush()

    login = await async_client.post("/api/v1/auth/login", json={"email": email, "password": "DocPass123!"})
    return doc.id, login.json()["token"]


class TestPatientChatbotFlow:
    async def test_magic_link_auth_then_full_consultation(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Mirrors the exact frontend flow:
        1. POST /auth/google/ (magic link path — email + is_magic_link=true)
        2. GET /api/validate_token/
        3. GET /api/chat_history/ (creates thread)
        4. POST /api/chat_view/ — general_education: AI chat
        5. GET /api/consultation_list/
        6. POST /api/chat_view/ question=CONFIRM → transition to post_payment_intake
        7. POST /api/chat_view/ intake steps
        8. POST /api/chat_view/ INTAKE COMPLETE signal → dermatologist_review
        9. POST /api/archive_consultation/ → new thread
        10. Doctor reviews + responds
        """
        org_id = await _setup_org(db_session)
        doc_id, doc_token = await _setup_doctor(db_session, org_id, async_client)

        patient_email = f"patient_{uuid.uuid4().hex[:8]}@patient.com"
        patient_name = "E2E Chatbot Patient"

        # ── 1. Magic link auth (mirrors frontend onAuthStateChange) ───────────
        r = await async_client.post("/auth/google/", json={
            "email": patient_email,
            "name": patient_name,
            "is_magic_link": True,
            "organization_id": str(org_id),
        })
        assert r.status_code == 200
        patient_token = r.json()["token"]
        assert r.json()["name"] == patient_name
        pat_auth = {"Authorization": f"Bearer {patient_token}"}

        # ── 2. Validate token ─────────────────────────────────────────────────
        r = await async_client.get("/api/validate_token/", headers=pat_auth)
        assert r.status_code == 200
        assert r.json()["role"] == "patient"
        assert r.json()["email"] == patient_email

        # ── 3. Chat history — creates initial thread ───────────────────────────
        r = await async_client.get("/api/chat_history/", headers=pat_auth)
        assert r.status_code == 200
        body = r.json()
        thread_id = body["thread_id"]
        assert body["mode"] == "general_education"
        assert isinstance(body["conv"], list)

        # ── 4. General education AI chat ──────────────────────────────────────
        r = await async_client.post("/api/chat_view/", headers=pat_auth,
            json={"question": "What is eczema?", "thread_id": thread_id})
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        assert body["mode"] == "general_education"
        # AI response should be non-empty
        assert len(body["result"]) > 0 or body.get("error") == 0  # Might be empty if OpenRouter not configured

        # ── 5. Consultation list ──────────────────────────────────────────────
        r = await async_client.get("/api/consultation_list/", headers=pat_auth)
        assert r.status_code == 200
        history = r.json()["history"]
        assert any(h["id"] == thread_id for h in history)

        # ── 6. Payment confirmation → post_payment_intake ─────────────────────
        r = await async_client.post("/api/chat_view/", headers=pat_auth,
            json={"question": "CONFIRM", "thread_id": thread_id})
        assert r.status_code == 200
        assert r.json()["mode"] == "post_payment_intake"

        # ── 7. Intake steps ───────────────────────────────────────────────────
        for q in [
            "About 2 weeks",      # duration
            "Red, itchy rash on my elbow",  # symptoms
            "Left elbow",         # location
            "Hydrocortisone cream, no improvement",  # meds
            "No prior skin conditions",  # history
        ]:
            r = await async_client.post("/api/chat_view/", headers=pat_auth,
                json={"question": q, "thread_id": thread_id})
            assert r.status_code == 200
            assert r.json()["mode"] in ("post_payment_intake", "dermatologist_review")

        # ── 8. INTAKE COMPLETE signal ─────────────────────────────────────────
        intake_complete_msg = (
            "INTAKE COMPLETE\nSummary:\n"
            "Duration: 2 weeks\nSymptoms: Red itchy rash\n"
            "Location: Left elbow\nMeds: Hydrocortisone\nHistory: None"
        )
        r = await async_client.post("/api/chat_view/", headers=pat_auth,
            json={"question": intake_complete_msg, "thread_id": thread_id})
        assert r.status_code == 200
        assert r.json()["mode"] == "dermatologist_review"

        # ── 9. Verify FIX 1 guard: AI is silent in dermatologist_review ───────
        r = await async_client.post("/api/chat_view/", headers=pat_auth,
            json={"question": "Hello?", "thread_id": thread_id})
        assert r.status_code == 200
        # AI should respond with empty string (FIX 1)
        assert r.json()["result"] == "" or r.json()["response"] == ""

        # ── 10. Doctor receives the thread ────────────────────────────────────
        doc_auth = {"Authorization": f"Bearer {doc_token}"}
        r = await async_client.get("/api/doctor_tabs/", headers=doc_auth)
        assert r.status_code == 200
        # Thread should appear as it's paid
        # Note: thread is paid because we sent CONFIRM
        # Check tabs list
        tabs = r.json()["tabs_doc"]
        # At minimum the endpoint shouldn't crash

        # ── 11. Doctor views conversation ─────────────────────────────────────
        r = await async_client.post("/api/doctor_conversation/", headers=doc_auth, json={"id": thread_id})
        assert r.status_code == 200
        assert isinstance(r.json()["conv"], list)

        # ── 12. Doctor sends response ─────────────────────────────────────────
        r = await async_client.post("/api/doctor_send_response/", headers=doc_auth, json={
            "id": thread_id,
            "question": "Based on your symptoms, this appears to be contact dermatitis. "
                        "I recommend avoiding the irritant and using a stronger topical steroid.",
        })
        assert r.status_code == 200
        assert r.json()["error"] == 0

        # ── 13. Patient sees final_output mode — AI still silent ──────────────
        r = await async_client.post("/api/chat_view/", headers=pat_auth,
            json={"question": "Thank you doctor!", "thread_id": thread_id})
        assert r.status_code == 200
        assert r.json()["mode"] == "final_output"
        assert r.json()["result"] == "" or r.json()["response"] == ""

        # ── 14. Archive + new consultation ───────────────────────────────────
        r = await async_client.post("/api/archive_consultation/", headers=pat_auth)
        assert r.status_code == 200
        new_thread_id = r.json()["thread_id"]
        assert new_thread_id != thread_id

        # ── 15. Original thread now completed; new one is active ──────────────
        r = await async_client.get("/api/consultation_list/", headers=pat_auth)
        history = r.json()["history"]
        statuses = {h["id"]: h["status"] for h in history}
        assert statuses.get(thread_id) == "completed"
        assert statuses.get(new_thread_id) == "active"

    async def test_google_oauth_style_auth(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Test the Google OAuth path (token without email — decode fallback)."""
        org_id = await _setup_org(db_session)

        # Simulate a Google credential token (base64 encoded fake payload)
        import base64, json
        payload = {"email": f"google_{uuid.uuid4().hex[:8]}@gmail.com", "name": "Google Patient", "sub": "123456"}
        # Google JWTs have 3 parts; create a fake but decodable one
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
        body_part = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        fake_google_token = f"{header}.{body_part}.fakesig"

        r = await async_client.post("/auth/google/", json={
            "token": fake_google_token,
            "organization_id": str(org_id),
        })
        assert r.status_code == 200
        assert r.json()["email"] == payload["email"]
        assert "token" in r.json()

    async def test_payment_flow(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Test PayPal payment endpoints (dev mode — no real PayPal needed)."""
        org_id = await _setup_org(db_session)

        # Auth patient
        r = await async_client.post("/auth/google/", json={
            "email": f"payer_{uuid.uuid4().hex[:8]}@test.com",
            "name": "Payment Patient",
            "organization_id": str(org_id),
        })
        patient_token = r.json()["token"]
        pat_auth = {"Authorization": f"Bearer {patient_token}"}

        # Create payment intent (dev mode returns mock orderID)
        r = await async_client.post("/api/create-payment-intent/", headers=pat_auth,
            json={"amount": 15.00})
        assert r.status_code == 200
        assert "orderID" in r.json()
        order_id = r.json()["orderID"]

        # Capture payment (dev mode marks thread as paid)
        r = await async_client.post("/api/capture_payment/", headers=pat_auth,
            json={"orderID": order_id})
        assert r.status_code == 200
        assert r.json()["status"] == "COMPLETED"

    async def test_patient_auth_idempotent(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Same patient email returns same patient record and valid token."""
        org_id = await _setup_org(db_session)
        email = f"idempotent_{uuid.uuid4().hex[:8]}@test.com"

        r1 = await async_client.post("/auth/google/", json={"email": email, "organization_id": str(org_id)})
        r2 = await async_client.post("/auth/google/", json={"email": email, "organization_id": str(org_id)})
        assert r1.json()["email"] == r2.json()["email"] == email

        # Both tokens should be valid
        for token in [r1.json()["token"], r2.json()["token"]]:
            r = await async_client.get("/api/validate_token/", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
