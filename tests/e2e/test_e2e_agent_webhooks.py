from __future__ import annotations

"""
E2E: Agent and webhook persona flows
Tests the complete LiveKit/Vobiz webhook pipeline, agent escalation,
patient phone lookup, and inbound call processing.
"""

import os
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _setup_hospital_with_phone(
    db_session: AsyncSession, vobiz_did: str
) -> uuid.UUID:
    from api.app.models.organization import HospitalConfig, Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Agent E2E Hospital {uuid.uuid4().hex[:6]}",
        slug=f"agent-e2e-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    db_session.add(
        HospitalConfig(
            id=uuid.uuid4(),
            organization_id=org.id,
            vobiz_did=vobiz_did,
            branding_json={},
            agent_prompt_prefix="You are the agent for this hospital.",
        )
    )
    await db_session.flush()
    return org.id


async def _seed_patient(
    db_session: AsyncSession, org_id: uuid.UUID, mobile: str
) -> uuid.UUID:
    from api.app.models.hospital.patient import Patient

    p = Patient(
        id=uuid.uuid4(),
        organization_id=org_id,
        patient_name="Agent Test Patient",
        mobile_no=mobile,
        department="General",
    )
    db_session.add(p)
    await db_session.flush()
    return p.id


class TestVobizInboundWebhookFlow:
    async def test_inbound_call_creates_record_and_links_to_org(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Vobiz calls our webhook with To=hospital_DID.
        We verify: correct org resolved via DID lookup → InboundCall created.
        No hardcoded phone numbers — pure DB lookup.
        """
        from api.app.models.hospital.call import InboundCall

        did = f"+91800{uuid.uuid4().int % 10000000:07d}"
        org_id = await _setup_hospital_with_phone(db_session, did)
        call_uuid = f"vobiz-{uuid.uuid4().hex[:12]}"

        r = await async_client.post(
            "/api/v1/webhooks/vobiz/inbound",
            json={
                "CallUUID": call_uuid,
                "CallStatus": "ringing",
                "From": "+919876543210",
                "To": did,
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "acknowledged"

        row = await db_session.scalar(
            select(InboundCall).where(InboundCall.livekit_call_id == call_uuid)
        )
        assert row is not None
        assert row.organization_id == org_id
        assert row.from_phone_number == "+919876543210"
        assert row.to_phone_number == did

    async def test_inbound_unknown_did_does_not_crash(
        self, async_client: AsyncClient
    ) -> None:
        r = await async_client.post(
            "/api/v1/webhooks/vobiz/inbound",
            json={
                "CallUUID": f"unknown-{uuid.uuid4().hex}",
                "CallStatus": "ringing",
                "From": "+919000000000",
                "To": "+910000000000",  # No hospital has this
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "acknowledged"

    async def test_inbound_did_suffix_match(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """DID suffix matching: To=+918049280487 matches vobiz_did=+918049280487."""
        from api.app.models.hospital.call import InboundCall

        did = "+918049280487"
        org_id = await _setup_hospital_with_phone(db_session, did)
        call_uuid = f"suffix-{uuid.uuid4().hex[:12]}"

        r = await async_client.post(
            "/api/v1/webhooks/vobiz/inbound",
            json={
                "CallUUID": call_uuid,
                "CallStatus": "ringing",
                "From": "+919111222333",
                "To": did,
            },
        )
        assert r.status_code == 200
        row = await db_session.scalar(
            select(InboundCall).where(InboundCall.livekit_call_id == call_uuid)
        )
        assert row is not None
        assert row.organization_id == org_id


class TestVobizOutboundWebhookFlow:
    async def test_outbound_status_update(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.call import OutboundCall
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name="Outbound Wh Org",
            slug=f"owh-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        room_id = f"out_{uuid.uuid4().hex[:12]}"
        call = OutboundCall(
            id=uuid.uuid4(),
            organization_id=org.id,
            livekit_call_id=room_id,
            status="queued",
            calling_process="queued",
        )
        db_session.add(call)
        await db_session.flush()

        r = await async_client.post(
            "/api/v1/webhooks/vobiz/outbound",
            json={
                "CallStatus": "completed",
                "CustomSIPHeaders": {"X-VH-CorrelationId": room_id},
            },
        )
        assert r.status_code == 200

        refreshed = await db_session.scalar(
            select(OutboundCall).where(OutboundCall.id == call.id)
        )
        assert refreshed.calling_process == "connected"
        assert refreshed.status == "ended"


class TestLiveKitWebhookFlow:
    async def test_room_finished_outbound(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.call import OutboundCall
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name="LK WH Org",
            slug=f"lkwh-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        room_id = f"out_{uuid.uuid4().hex[:12]}"
        db_session.add(
            OutboundCall(
                id=uuid.uuid4(), organization_id=org.id, livekit_call_id=room_id
            )
        )
        await db_session.flush()

        r = await async_client.post(
            "/api/v1/webhooks/livekit",
            json={
                "event": "room_finished",
                "room": {"name": room_id},
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "received"

    async def test_room_finished_inbound_room_prefix(
        self, async_client: AsyncClient
    ) -> None:
        r = await async_client.post(
            "/api/v1/webhooks/livekit",
            json={
                "event": "room_finished",
                "room": {"name": "inbound_+910000000001_abc123"},
            },
        )
        assert r.status_code == 200

    async def test_whatsapp_delivery_status(self, async_client: AsyncClient) -> None:
        r = await async_client.post(
            "/api/v1/webhooks/vobiz/whatsapp",
            json={
                "event": "message.status",
                "channel_id": "ch_test",
                "data": {
                    "message_id": "msg_123",
                    "status": "delivered",
                    "to": "+919000000000",
                },
            },
        )
        assert r.status_code == 200


class TestAgentEscalationFlow:
    async def test_escalation_by_phone_e2e(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Agent calls the internal escalation endpoint with phone number."""
        from api.app.models.hospital.call import Escalation

        did = f"+91900{uuid.uuid4().hex[:7]}"
        org_id = await _setup_hospital_with_phone(db_session, did)
        mobile = f"+91800{uuid.uuid4().hex[:7]}"
        patient_id = await _seed_patient(db_session, org_id, mobile)
        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")

        r = await async_client.post(
            "/api/internal/escalation/",
            headers={"X-Internal-API-Secret": secret},
            json={
                "phone_number": mobile,
                "issue_description": "Patient distressed about medication side effects",
                "department": "General",
            },
        )
        assert r.status_code == 200
        assert r.json()["error"] == 0
        assert r.json()["msg"] == "Escalation recorded"

        esc = await db_session.scalar(
            select(Escalation).where(Escalation.patient_id == patient_id)
        )
        assert esc is not None
        assert "medication side effects" in esc.issue_description
        assert esc.organization_id == org_id

    async def test_patient_phone_lookup_e2e(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Agent looks up patient by phone — gets full history."""
        did = f"+91700{uuid.uuid4().hex[:7]}"
        org_id = await _setup_hospital_with_phone(db_session, did)
        mobile = f"+91600{uuid.uuid4().int % 10000000:07d}"
        await _seed_patient(db_session, org_id, mobile)

        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
        r = await async_client.get(
            f"/api/patient_lookup_by_phone/?phone_number={mobile}",
            headers={"X-Internal-API-Secret": secret},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["error"] == 0
        assert body["count"] == 1
        assert body["data"][0]["mobile_no"] == mobile
        assert "call_history" in body["data"][0]
        # All history sections must be present
        ch = body["data"][0]["call_history"]
        assert "outbound_calls" in ch
        assert "inbound_calls" in ch
        assert "feedback" in ch
        assert "escalations" in ch

    async def test_phone_lookup_returns_full_call_history(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Verify call history is populated when calls exist."""
        from api.app.models.hospital.call import CallFeedback, Escalation

        did = f"+91500{uuid.uuid4().hex[:7]}"
        org_id = await _setup_hospital_with_phone(db_session, did)
        mobile = f"+91400{uuid.uuid4().int % 10000000:07d}"
        patient_id = await _seed_patient(db_session, org_id, mobile)

        # Seed feedback and escalation
        from datetime import datetime, timezone

        db_session.add(
            CallFeedback(
                id=uuid.uuid4(),
                organization_id=org_id,
                patient_id=patient_id,
                call_status="connected",
                call_outcome="positive",
                called_at=datetime.now(tz=timezone.utc),
            )
        )
        db_session.add(
            Escalation(
                id=uuid.uuid4(),
                organization_id=org_id,
                patient_id=patient_id,
                issue_description="High BP reading",
                status="pending",
            )
        )
        await db_session.flush()

        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
        r = await async_client.get(
            f"/api/patient_lookup_by_phone/?phone_number={mobile}",
            headers={"X-Internal-API-Secret": secret},
        )
        body = r.json()
        ch = body["data"][0]["call_history"]
        assert len(ch["feedback"]) >= 1
        assert len(ch["escalations"]) >= 1
        assert ch["feedback"][0]["call_outcome"] == "positive"
        assert ch["escalations"][0]["issue_description"] == "High BP reading"


class TestMultiTenancyHardening:
    async def test_jwt_org_id_cannot_be_spoofed(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        A user from Org A cannot access patients from Org B
        even if they manually craft requests.
        The organization_id is ALWAYS derived from the JWT, not the request body.
        """
        from api.app.core.security import hash_password
        from api.app.models.auth import User
        from api.app.models.hospital.patient import Patient
        from api.app.models.organization import Organization

        org_a = Organization(
            id=uuid.uuid4(),
            name="OrgA MT",
            slug=f"orga-mt-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        org_b = Organization(
            id=uuid.uuid4(),
            name="OrgB MT",
            slug=f"orgb-mt-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add_all([org_a, org_b])
        await db_session.flush()  # Must flush orgs before adding FKs

        email_a = f"staff_a_{uuid.uuid4().hex[:8]}@a.com"
        user_a = User(
            id=uuid.uuid4(),
            organization_id=org_a.id,
            email=email_a,
            password_hash=hash_password("PassA123!"),
            name="Staff A",
            role="hospital_user",
        )
        db_session.add(user_a)

        patient_b = Patient(
            id=uuid.uuid4(),
            organization_id=org_b.id,
            patient_name="Secret Patient B",
            mobile_no="+919111222444",
        )
        db_session.add(patient_b)
        await db_session.flush()

        # Login as org A user
        login = await async_client.post(
            "/api/v1/auth/login", json={"email": email_a, "password": "PassA123!"}
        )
        token_a = login.json()["token"]
        auth_a = {"Authorization": f"Bearer {token_a}"}

        # Try to access org B's patient via call history (should return 404)
        r = await async_client.get(
            f"/api/v1/patients/{patient_b.id}/history", headers=auth_a
        )
        assert r.status_code == 404

        # Patient list must not include org B patient
        r = await async_client.get("/api/v1/patients/", headers=auth_a)
        names = [p["patient_name"] for p in r.json()["data"]]
        assert "Secret Patient B" not in names

    async def test_inbound_feedback_wrong_org_rejected(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Hospital staff cannot post feedback for inbound calls from another org."""
        from api.app.core.security import hash_password
        from api.app.models.auth import User
        from api.app.models.hospital.call import InboundCall
        from api.app.models.organization import Organization

        org_a = Organization(
            id=uuid.uuid4(),
            name="Org A Inb",
            slug=f"orga-inb-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        org_b = Organization(
            id=uuid.uuid4(),
            name="Org B Inb",
            slug=f"orgb-inb-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add_all([org_a, org_b])
        await db_session.flush()  # Flush orgs before adding FKs

        email_a = f"staff_inb_{uuid.uuid4().hex[:8]}@a.com"
        db_session.add(
            User(
                id=uuid.uuid4(),
                organization_id=org_a.id,
                email=email_a,
                password_hash=hash_password("PassInb123!"),
                name="Staff Inb A",
                role="hospital_user",
            )
        )

        call_b = InboundCall(
            id=uuid.uuid4(), organization_id=org_b.id, from_phone_number="+919111000000"
        )
        db_session.add(call_b)
        await db_session.flush()

        login = await async_client.post(
            "/api/v1/auth/login", json={"email": email_a, "password": "PassInb123!"}
        )
        auth_a = {"Authorization": f"Bearer {login.json()['token']}"}

        r = await async_client.post(
            "/api/v1/inbound/feedback",
            headers=auth_a,
            json={
                "patient_id": str(call_b.id),
                "call_status": "connected",
            },
        )
        assert r.status_code == 404
