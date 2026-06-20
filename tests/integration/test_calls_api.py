from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _setup_hospital(async_client: AsyncClient, db_session: AsyncSession) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.hospital.patient import Patient
    from api.app.models.organization import HospitalConfig, Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Calls Test Hospital {uuid.uuid4().hex[:6]}",
        slug=f"calls-test-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    HospitalConfig(
        organization_id=org.id,
        id=uuid.uuid4(),
        vobiz_did="+911234567891",
        branding_json={},
    )

    email = f"calls_{uuid.uuid4().hex[:8]}@hospital.com"
    password = "TestPass123!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        name="Calls User",
        role="hospital_user",
        perm_calllog_engagement=True,
    )
    db_session.add(user)

    patient = Patient(
        id=uuid.uuid4(),
        organization_id=org.id,
        patient_name="Call Target",
        mobile_no="+919000000099",
        department="Cardiology",
    )
    db_session.add(patient)
    await db_session.flush()

    login = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = login.json()["token"]
    return org.id, token, patient.id


class TestOutboundCallDispatch:
    async def test_individual_call_queued(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, patient_id = await _setup_hospital(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/calls/outbound",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "is_individual": True,
                "patient_ids": [str(patient_id)],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        # LiveKit won't be reachable in test, so calls should be queued/recorded
        assert "patients" in body or "msg" in body

    async def test_campaign_trigger(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, _ = await _setup_hospital(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/calls/outbound",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "is_individual": False,
                "name": "Test Campaign",
                "audience_type": "all",
                "purpose": "Health checkup reminder",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "campaign_id" in body

    async def test_outbound_requires_auth(self, async_client: AsyncClient) -> None:
        resp = await async_client.post(
            "/api/v1/calls/outbound", json={"is_individual": True}
        )
        assert resp.status_code in (401, 403)


class TestInboundList:
    async def test_list_inbound_calls(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.call import InboundCall

        org_id, token, _ = await _setup_hospital(async_client, db_session)

        db_session.add(
            InboundCall(
                id=uuid.uuid4(),
                organization_id=org_id,
                from_phone_number="+919111111111",
                to_phone_number="+911234567891",
                calling_process="connected",
            )
        )
        await db_session.flush()

        resp = await async_client.get(
            "/api/v1/inbound/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert len(body["data"]) >= 1

    async def test_inbound_scoped_to_org(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.call import InboundCall
        from api.app.models.organization import Organization

        org_id, token, _ = await _setup_hospital(async_client, db_session)

        # Add a call to a different org
        other_org = Organization(
            id=uuid.uuid4(),
            name="Other",
            slug=f"other-calls-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(other_org)
        await db_session.flush()
        db_session.add(
            InboundCall(
                id=uuid.uuid4(),
                organization_id=other_org.id,
                from_phone_number="+919222222222",
            )
        )
        # Add a call to our org
        db_session.add(
            InboundCall(
                id=uuid.uuid4(),
                organization_id=org_id,
                from_phone_number="+919333333333",
            )
        )
        await db_session.flush()

        resp = await async_client.get(
            "/api/v1/inbound/", headers={"Authorization": f"Bearer {token}"}
        )
        body = resp.json()
        # Should only see our org's call
        for item in body["data"]:
            assert item.get("from_phone_number") != "+919222222222"


class TestExcelExport:
    async def test_excel_export_returns_xlsx(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, _ = await _setup_hospital(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/calls/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers.get("content-type", "")


class TestMediVoiceSessions:
    async def _setup_doctor(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> tuple:
        from api.app.core.security import hash_password
        from api.app.models.auth import User
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name=f"MV Org {uuid.uuid4().hex[:6]}",
            slug=f"mv-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        email = f"doctor_{uuid.uuid4().hex[:8]}@hospital.com"
        password = "DoctorPass123!"
        doctor = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=email,
            password_hash=hash_password(password),
            name="Dr. Test",
            role="doctor",
            department="Cardiology",
        )
        db_session.add(doctor)
        await db_session.flush()

        login = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        token = login.json()["token"]
        return org.id, token, doctor.id

    async def test_create_medivoice_session(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, doctor_id = await self._setup_doctor(async_client, db_session)
        resp = await async_client.post(
            "/api/v1/medivoice/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "patient_name": "Test Patient",
                "patient_mobile": "+919000000099",
                "patient_email": "patient@test.com",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "session_id" in body

    async def test_list_medivoice_sessions(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, doctor_id = await self._setup_doctor(async_client, db_session)
        # Create a session first
        await async_client.post(
            "/api/v1/medivoice/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"patient_name": "Patient X", "patient_mobile": "+919000000001"},
        )
        resp = await async_client.get(
            "/api/v1/medivoice/sessions", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert len(body["sessions"]) >= 1


class TestDoctorScheduleEndpoint:
    async def test_doctor_schedule_with_internal_secret(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        import os

        from api.app.core.security import hash_password
        from api.app.models.auth import User
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name=f"Doc Sched Org {uuid.uuid4().hex[:6]}",
            slug=f"ds-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        doctor = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=f"doc_{uuid.uuid4().hex[:8]}@hospital.com",
            password_hash=hash_password("pw"),
            name="Dr. Schedule",
            role="doctor",
            department="Neurology",
            availability_json=[{"day": "Monday", "slots": ["09:00"]}],
            consultation_fee=1000,
        )
        db_session.add(doctor)
        await db_session.flush()

        secret = os.environ.get("INTERNAL_API_SHARED_SECRET", "test-internal-secret")
        resp = await async_client.get(
            f"/api/v1/tenant/doctors?organization_id={org.id}",
            headers={"X-Internal-API-Secret": secret},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert any(d["doctor_name"] == "Dr. Schedule" for d in body)

    async def test_doctor_schedule_without_secret_rejected(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.get(
            f"/api/v1/tenant/doctors?organization_id={uuid.uuid4()}"
        )
        assert resp.status_code in (401, 403)
