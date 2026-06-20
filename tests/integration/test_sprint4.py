from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _hospital_setup(async_client: AsyncClient, db_session: AsyncSession) -> tuple:
    """Returns (org_id, token, patient_id, doctor_id)."""
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.hospital.call import (CallFeedback, Escalation,
                                              InboundCall)
    from api.app.models.hospital.patient import Patient
    from api.app.models.organization import HospitalConfig, Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Sprint4 Hospital {uuid.uuid4().hex[:6]}",
        slug=f"s4-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    HospitalConfig(
        id=uuid.uuid4(),
        organization_id=org.id,
        vobiz_did="+919000000099",
        branding_json={},
    )

    email = f"s4_{uuid.uuid4().hex[:8]}@hospital.com"
    password = "TestPass123!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        name="S4 Admin",
        role="hospital_admin",
    )
    db_session.add(user)

    doctor = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=f"doc_{uuid.uuid4().hex[:8]}@hospital.com",
        password_hash=hash_password("DocPass123!"),
        name="Dr. Sprint",
        role="doctor",
        department="Cardiology",
        consultation_fee=500,
        timings="9am-5pm",
    )
    db_session.add(doctor)

    patient = Patient(
        id=uuid.uuid4(),
        organization_id=org.id,
        patient_name="ROI Patient",
        mobile_no="+919500000001",
        department="Cardiology",
    )
    db_session.add(patient)
    await db_session.flush()

    # Seed call feedback
    now = datetime.now(tz=timezone.utc)
    db_session.add(
        CallFeedback(
            id=uuid.uuid4(),
            organization_id=org.id,
            patient_id=patient.id,
            call_status="connected",
            call_outcome="positive",
            call_duration=120,
            called_at=now - timedelta(days=1),
        )
    )
    db_session.add(
        Escalation(
            id=uuid.uuid4(),
            organization_id=org.id,
            patient_id=patient.id,
            issue_description="Test",
            department="Cardiology",
        )
    )
    db_session.add(
        InboundCall(
            id=uuid.uuid4(),
            organization_id=org.id,
            from_phone_number="+919500000001",
            calling_process="connected",
        )
    )
    await db_session.flush()

    login = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = login.json()["token"]
    return org.id, token, patient.id, doctor.id


class TestROIMetrics:
    async def test_roi_metrics_outbound(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/roi?call_direction=outbound",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "roi_financial" in body
        assert "roi_efficiency" in body
        # Verify structure
        fin = body["roi_financial"]
        assert any(item["name"] == "Revenue Influenced" for item in fin)
        assert any(item["name"] == "Leakage Prevented" for item in fin)

    async def test_roi_metrics_inbound(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/roi?call_direction=inbound",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "roi_efficiency" in body
        eff = body["roi_efficiency"]
        assert any(item["name"] == "Staff Hours Saved" for item in eff)

    async def test_roi_metrics_date_range(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/roi?start_date=2024-01-01&end_date=2024-12-31",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


class TestDepartmentAnalytics:
    async def test_departments_outbound(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/departments?call_direction=outbound",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "department_table" in body
        assert "top_intents" in body
        # Check structure of table
        for row in body["department_table"]:
            assert "department" in row
            assert "interactions" in row
            assert "bookings" in row
            assert "conversion" in row
            assert "revenue" in row

    async def test_departments_inbound(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/departments?call_direction=inbound",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


class TestCampaignCRUD:
    async def test_list_campaigns(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.campaign import Campaign

        org_id, token, _, _ = await _hospital_setup(async_client, db_session)

        db_session.add(
            Campaign(
                id=uuid.uuid4(),
                organization_id=org_id,
                name="Test Campaign",
                status="created",
            )
        )
        await db_session.flush()

        resp = await async_client.get(
            "/api/v1/campaigns/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert len(body["data"]) >= 1
        assert body["data"][0]["name"] == "Test Campaign"

    async def test_delete_campaign(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.campaign import Campaign

        org_id, token, _, _ = await _hospital_setup(async_client, db_session)

        camp = Campaign(id=uuid.uuid4(), organization_id=org_id, name="Delete Me")
        db_session.add(camp)
        await db_session.flush()

        resp = await async_client.delete(
            f"/api/v1/campaigns/{camp.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["error"] == 0

    async def test_campaign_scoped_to_org(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.campaign import Campaign
        from api.app.models.organization import Organization

        org_id, token, _, _ = await _hospital_setup(async_client, db_session)
        other_org = Organization(
            id=uuid.uuid4(),
            name="Other",
            slug=f"oth-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(other_org)
        await db_session.flush()
        db_session.add(
            Campaign(
                id=uuid.uuid4(), organization_id=other_org.id, name="Other Org Campaign"
            )
        )
        await db_session.flush()

        resp = await async_client.get(
            "/api/v1/campaigns/", headers={"Authorization": f"Bearer {token}"}
        )
        names = [c["name"] for c in resp.json()["data"]]
        assert "Other Org Campaign" not in names


class TestDoctorManagement:
    async def test_list_doctors(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, doctor_id = await _hospital_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/doctors/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert len(body["data"]) >= 1
        assert any(d["name"] == "Dr. Sprint" for d in body["data"])

    async def test_create_doctor(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/doctors/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Dr. New",
                "email": f"dr_new_{uuid.uuid4().hex[:8]}@hospital.com",
                "password": "DrPass123!",
                "department": "Neurology",
                "consultation_fee": 800,
                "timings": "10am-4pm",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "id" in body

    async def test_staff_availability(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/doctors/availability",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert isinstance(body["data"], list)


class TestWhatsAppBroadcast:
    async def test_broadcast_text_saved(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/calls/broadcast",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "Health update from the hospital!"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0

    async def test_get_broadcast_text(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token, _, _ = await _hospital_setup(async_client, db_session)
        # First set the text
        await async_client.post(
            "/api/v1/calls/broadcast",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "Test broadcast text"},
        )
        resp = await async_client.get(
            "/api/v1/calls/broadcast",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "data" in body


class TestPatientPhoneLookup:
    async def test_lookup_known_patient(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        import os

        _, _, patient_id, _ = await _hospital_setup(async_client, db_session)
        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")

        resp = await async_client.get(
            "/api/patient_lookup_by_phone/?phone_number=+919500000001",
            headers={"X-Internal-API-Secret": secret},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert body["count"] >= 1
        assert body["data"][0]["patient_name"] == "ROI Patient"

    async def test_lookup_without_secret_rejected(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.get(
            "/api/patient_lookup_by_phone/?phone_number=+919500000001"
        )
        assert resp.status_code in (401, 403)

    async def test_lookup_unknown_number(self, async_client: AsyncClient) -> None:
        import os

        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
        resp = await async_client.get(
            "/api/patient_lookup_by_phone/?phone_number=+919999999999",
            headers={"X-Internal-API-Secret": secret},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 1


class TestMediVoiceSync:
    async def test_sync_creates_session(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        import os

        org_id, token, _, doctor_id = await _hospital_setup(async_client, db_session)
        # Need doctor email for sync
        from sqlalchemy import select

        from api.app.models.auth import User

        doctor = await db_session.scalar(select(User).where(User.id == doctor_id))

        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
        resp = await async_client.post(
            "/api/v1/medivoice/sync",
            headers={"X-Fettle-Secret": secret},
            json={
                "doctorEmail": doctor.email,
                "patientName": "Sync Patient",
                "patientMobile": "+919000000099",
                "overallSummary": "Checkup summary",
                "diagnosis": "Hypertension",
                "medicines": [{"name": "Amlodipine", "dosage": "5mg"}],
                "transcriptions": [
                    {"speaker": "doctor", "text": "How are you?", "timestamp": 0.0}
                ],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "session_id" in body
