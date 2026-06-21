from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _full_setup(async_client: AsyncClient, db_session: AsyncSession) -> tuple:
    """Create org + admin user + patients + feedback. Returns (org_id, token)."""
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.hospital.call import CallFeedback, Escalation
    from api.app.models.hospital.patient import Patient
    from api.app.models.organization import Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Dashboard Test Hospital {uuid.uuid4().hex[:6]}",
        slug=f"dash-test-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    email = f"dash_{uuid.uuid4().hex[:8]}@hospital.com"
    password = "DashPass123!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        name="Dashboard Admin",
        role="hospital_admin",
    )
    db_session.add(user)
    await db_session.flush()

    # Create 3 patients
    patients = []
    for i in range(3):
        p = Patient(
            id=uuid.uuid4(),
            organization_id=org.id,
            patient_name=f"Patient {i}",
            mobile_no=f"+9180000{i:05d}",
            department="General",
        )
        db_session.add(p)
        patients.append(p)
    await db_session.flush()

    # Create call feedback
    now = datetime.now(tz=timezone.utc)
    for i, p in enumerate(patients):
        db_session.add(
            CallFeedback(
                id=uuid.uuid4(),
                organization_id=org.id,
                patient_id=p.id,
                call_status="connected" if i < 2 else "not_connected",
                call_outcome="positive" if i == 0 else "no_feedback",
                call_duration=120,
                called_at=now - timedelta(days=i),
            )
        )
    await db_session.flush()

    # Create escalation
    db_session.add(
        Escalation(
            id=uuid.uuid4(),
            organization_id=org.id,
            patient_id=patients[0].id,
            issue_description="Test escalation",
            status="pending",
            department="General",
        )
    )
    await db_session.flush()

    login = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = login.json()["token"]
    return org.id, token


class TestOutboundKPI:
    async def test_kpi_summary_returns_four_cards(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/kpi",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "total_patients_contacted" in body
        assert "call_answer_rate" in body
        assert "community_conversion" in body
        assert "escalated_issues" in body

    async def test_kpi_cards_have_required_fields(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/kpi",
            headers={"Authorization": f"Bearer {token}"},
        )
        card = resp.json()["total_patients_contacted"]
        assert "title" in card
        assert "value" in card
        assert "change" in card
        assert "trend" in card
        assert "icon" in card
        assert "color" in card

    async def test_kpi_date_range_filter(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/kpi?start_date=2024-01-01&end_date=2024-12-31",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


class TestAdminDashboard:
    async def test_admin_dashboard_returns_counts(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/admin",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "patientsCount" in body
        assert "callCount" in body
        assert "escalationCount" in body
        assert "callAnswerRate" in body


class TestPatientEngagement:
    async def test_patient_engagement_structure(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/patient-engagement",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "contactsData" in body
        assert "callAnswerData" in body
        assert "metadata" in body
        assert isinstance(body["contactsData"], list)
        assert isinstance(body["callAnswerData"], list)

    async def test_call_answer_data_has_name_and_value(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/patient-engagement",
            headers={"Authorization": f"Bearer {token}"},
        )
        for item in resp.json()["callAnswerData"]:
            assert "name" in item
            assert "value" in item


class TestCommunityEngagement:
    async def test_community_engagement_structure(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/community-engagement",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "communityGrowthData" in body
        assert "departmentEngagementData" in body
        assert "feedbackDistribution" in body
        assert "metadata" in body
        assert "metrics" in body


class TestEscalationEngagement:
    async def test_escalation_engagement_structure(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, token = await _full_setup(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/dashboard/escalation-engagement",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "departmentEscalationData" in body
        assert "resolutionStatusData" in body
        assert "recentEscalations" in body
        assert "metadata" in body


class TestCallFeedbackPost:
    async def test_post_call_feedback(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.patient import Patient

        org_id, token = await _full_setup(async_client, db_session)

        p = Patient(
            id=uuid.uuid4(),
            organization_id=org_id,
            patient_name="Feedback Patient",
            mobile_no="+919500000001",
        )
        db_session.add(p)
        await db_session.flush()

        resp = await async_client.post(
            "/api/v1/calls/feedback",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "patient_id": str(p.id),
                "call_status": "connected",
                "call_outcome": "positive",
                "remarks": "Patient was satisfied",
                "call_duration": 90,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["error"] == 0

    async def test_post_feedback_wrong_org_patient_fails(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.patient import Patient
        from api.app.models.organization import Organization

        _, token = await _full_setup(async_client, db_session)

        other_org = Organization(
            id=uuid.uuid4(),
            name="Other Org",
            slug=f"other3-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(other_org)
        await db_session.flush()
        other_patient = Patient(
            id=uuid.uuid4(),
            organization_id=other_org.id,
            patient_name="Other",
            mobile_no="+919600000001",
        )
        db_session.add(other_patient)
        await db_session.flush()

        resp = await async_client.post(
            "/api/v1/calls/feedback",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "patient_id": str(other_patient.id),
                "call_status": "connected",
                "call_outcome": "positive",
            },
        )
        assert resp.status_code in (400, 404)
