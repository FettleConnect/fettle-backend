from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _setup_org_and_token(
    async_client: AsyncClient, db_session: AsyncSession
) -> tuple:
    """Returns (org_id, token, user_id)."""
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.organization import Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Patient API Test Org {uuid.uuid4().hex[:6]}",
        slug=f"patient-api-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    email = f"patient_test_{uuid.uuid4().hex[:8]}@hospital.com"
    password = "TestPass123!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        name="Patient Test User",
        role="hospital_user",
        perm_patient_engagement=True,
        perm_upload_engagement=True,
        perm_calllog_engagement=True,
    )
    db_session.add(user)
    await db_session.flush()

    login = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = login.json()["token"]
    return org.id, token, user.id


async def _seed_patients(
    db_session: AsyncSession, org_id: uuid.UUID, count: int = 5
) -> list:
    from api.app.models.hospital.patient import Patient

    patients = []
    for i in range(count):
        p = Patient(
            id=uuid.uuid4(),
            organization_id=org_id,
            patient_name=f"Patient {i+1}",
            mobile_no=f"+9190000{i:05d}",
            department="Cardiology",
            age=30 + i,
        )
        db_session.add(p)
        patients.append(p)
    await db_session.flush()
    return patients


class TestFetchPatients:
    async def test_list_patients_authenticated(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, _ = await _setup_org_and_token(async_client, db_session)
        await _seed_patients(db_session, org_id, 3)
        resp = await async_client.get(
            "/api/v1/patients/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "data" in body
        assert body["count"] >= 3

    async def test_list_patients_unauthenticated(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.get("/api/v1/patients/")
        assert resp.status_code in (401, 403)

    async def test_list_patients_scoped_to_org(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.patient import Patient
        from api.app.models.organization import Organization

        # Create two orgs
        org1_id, token1, _ = await _setup_org_and_token(async_client, db_session)
        org2 = Organization(
            id=uuid.uuid4(),
            name=f"Other Org {uuid.uuid4().hex[:6]}",
            slug=f"other-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org2)
        await db_session.flush()

        # Seed patients in org2
        for i in range(3):
            db_session.add(
                Patient(
                    id=uuid.uuid4(),
                    organization_id=org2.id,
                    patient_name=f"Other Patient {i}",
                    mobile_no=f"+9198765{i:05d}",
                )
            )
        await db_session.flush()

        # Seed patients in org1
        await _seed_patients(db_session, org1_id, 2)

        resp = await async_client.get(
            "/api/v1/patients/",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Should only see org1's 2 patients, not org2's 3
        assert body["count"] == 2

    async def test_list_patients_dept_filter(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.patient import Patient

        org_id, token, _ = await _setup_org_and_token(async_client, db_session)
        db_session.add(
            Patient(
                id=uuid.uuid4(),
                organization_id=org_id,
                patient_name="Ortho Patient",
                mobile_no="+919111111111",
                department="Orthopedics",
            )
        )
        db_session.add(
            Patient(
                id=uuid.uuid4(),
                organization_id=org_id,
                patient_name="Cardio Patient",
                mobile_no="+919222222222",
                department="Cardiology",
            )
        )
        await db_session.flush()

        resp = await async_client.get(
            "/api/v1/patients/?department=Orthopedics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(p["department"] == "Orthopedics" for p in body["data"])

    async def test_list_patients_limit(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, _ = await _setup_org_and_token(async_client, db_session)
        await _seed_patients(db_session, org_id, 10)
        resp = await async_client.get(
            "/api/v1/patients/?limit=3",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) <= 3


class TestPatientCallHistory:
    async def test_call_history_returns_empty_for_new_patient(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, _ = await _setup_org_and_token(async_client, db_session)
        patients = await _seed_patients(db_session, org_id, 1)
        resp = await async_client.get(
            f"/api/v1/patients/{patients[0].id}/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert isinstance(body["history"], list)

    async def test_call_history_wrong_org_returns_404(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.patient import Patient
        from api.app.models.organization import Organization

        _org_id, token, _ = await _setup_org_and_token(async_client, db_session)
        other_org = Organization(
            id=uuid.uuid4(),
            name="Other",
            slug=f"other2-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(other_org)
        await db_session.flush()
        other_patient = Patient(
            id=uuid.uuid4(),
            organization_id=other_org.id,
            patient_name="Other",
            mobile_no="+910000099999",
        )
        db_session.add(other_patient)
        await db_session.flush()

        resp = await async_client.get(
            f"/api/v1/patients/{other_patient.id}/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


class TestUploadLogs:
    async def test_upload_logs_returns_list(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.patient import HospitalUploadLog

        org_id, token, _ = await _setup_org_and_token(async_client, db_session)
        db_session.add(
            HospitalUploadLog(
                id=uuid.uuid4(),
                organization_id=org_id,
                file_names=["test.csv"],
                status="SUCCESS",
                message="3 records",
            )
        )
        await db_session.flush()
        resp = await async_client.get(
            "/api/v1/patients/upload-logs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert len(body["data"]) >= 1
