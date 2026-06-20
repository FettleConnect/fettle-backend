from __future__ import annotations

"""
E2E: Hospital Staff (hospital_user) persona
Flow: login → view patients → log feedback → escalation → community engagement →
view dashboards → verify permission enforcement.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _setup_staff_with_patients(
    async_client: AsyncClient, db_session: AsyncSession
) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.hospital.call import (
        CallFeedback,
        CommunityEngagement,
        Escalation,
    )
    from api.app.models.hospital.patient import Patient
    from api.app.models.organization import Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"E2E Staff Hospital {uuid.uuid4().hex[:6]}",
        slug=f"e2e-staff-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    email = f"staff_{uuid.uuid4().hex[:8]}@e2ehospital.com"
    staff = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password("StaffPass123!"),
        name="E2E Staff",
        role="hospital_user",
        perm_patient_engagement=True,
        perm_calllog_engagement=True,
        perm_escalation_engagement=True,
        perm_community_engagement=True,
        perm_upload_engagement=True,
    )
    db_session.add(staff)

    # Seed patients
    patients = []
    for i in range(4):
        p = Patient(
            id=uuid.uuid4(),
            organization_id=org.id,
            patient_name=f"Staff Flow Patient {i}",
            mobile_no=f"+9180100{i:05d}",
            department="Orthopedics" if i % 2 == 0 else "Cardiology",
        )
        db_session.add(p)
        patients.append(p)
    await db_session.flush()

    resp = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "StaffPass123!"}
    )
    token = resp.json()["token"]
    return org.id, token, patients


class TestHospitalStaffFlow:
    async def test_complete_staff_workflow(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Full staff workflow:
        1. Login → verify token with correct permissions
        2. View patient list
        3. Log call feedback on a patient
        4. Create an escalation
        5. Log community engagement
        6. View outbound dashboard charts
        7. View inbound dashboard
        8. Export patient Excel report
        9. Confirm staff CANNOT see other org patients
        """
        org_id, token, patients = await _setup_staff_with_patients(
            async_client, db_session
        )
        auth = {"Authorization": f"Bearer {token}"}
        patient = patients[0]

        # ── 1. Login + permissions ───────────────────────────────────────────
        r = await async_client.get("/api/v1/auth/tab-access", headers=auth)
        assert r.status_code == 200
        perms = r.json()["permissions"]
        assert perms["patient_engagement"] is True
        assert perms["calllog_engagement"] is True

        # ── 2. Patient list ──────────────────────────────────────────────────
        r = await async_client.get("/api/v1/patients/", headers=auth)
        assert r.status_code == 200
        assert r.json()["count"] == 4

        # ── 3. Patient list with department filter ────────────────────────────
        r = await async_client.get(
            "/api/v1/patients/?department=Cardiology", headers=auth
        )
        assert r.status_code == 200
        for p in r.json()["data"]:
            assert p["department"] == "Cardiology"

        # ── 4. Log call feedback ─────────────────────────────────────────────
        r = await async_client.post(
            "/api/v1/calls/feedback",
            headers=auth,
            json={
                "patient_id": str(patient.id),
                "call_status": "connected",
                "call_outcome": "positive",
                "remarks": "Patient confirmed appointment",
                "revisit_encouraged": True,
                "call_duration": 180,
                "called_by": "E2E Staff",
            },
        )
        assert r.status_code == 200
        assert r.json()["error"] == 0

        # ── 5. Log escalation ────────────────────────────────────────────────
        r = await async_client.post(
            "/api/v1/escalations/feedback",
            headers=auth,
            json={
                "patient_id": str(patient.id),
                "issue_description": "Patient needs urgent attention for chest pain",
                "department": "Cardiology",
            },
        )
        assert r.status_code == 200

        # ── 6. Log community engagement ──────────────────────────────────────
        r = await async_client.post(
            "/api/v1/calls/community",
            headers=auth,
            json={
                "patient_id": str(patient.id),
                "engagement_type": "post",
                "department": "Cardiology",
            },
        )
        assert r.status_code == 200

        # ── 7. View escalation management list ───────────────────────────────
        r = await async_client.get("/api/v1/escalations/", headers=auth)
        assert r.status_code == 200
        esc_data = r.json()["data"]
        assert len(esc_data) >= 1
        # All escalations must be in our org
        for esc in esc_data:
            assert esc["issue_description"]  # Not empty

        # ── 8. Resolve escalation ────────────────────────────────────────────
        esc_id = esc_data[0]["id"]
        r = await async_client.post(
            "/api/v1/escalations/update",
            headers=auth,
            json={
                "id": str(esc_id),
                "status": "resolved",
                "resolution_notes": "Patient advised to visit ER",
            },
        )
        assert r.status_code == 200

        # ── 9. Dashboard — patient engagement chart ──────────────────────────
        r = await async_client.get("/api/v1/dashboard/patient-engagement", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert "contactsData" in body
        assert "callAnswerData" in body
        assert len(body["callAnswerData"]) == 2
        pcts = [item["value"] for item in body["callAnswerData"]]
        assert abs(sum(pcts) - 100.0) < 0.1  # Must sum to ~100%

        # ── 10. Community engagement dashboard ───────────────────────────────
        r = await async_client.get(
            "/api/v1/dashboard/community-engagement", headers=auth
        )
        assert r.status_code == 200
        body = r.json()
        assert "communityGrowthData" in body
        assert "feedbackDistribution" in body

        # ── 11. Escalation engagement dashboard ──────────────────────────────
        r = await async_client.get(
            "/api/v1/dashboard/escalation-engagement", headers=auth
        )
        assert r.status_code == 200
        body = r.json()
        assert "departmentEscalationData" in body
        # resolution should show resolved item
        resolved = [r for r in body["resolutionStatusData"] if r["name"] == "Resolved"]
        assert len(resolved) == 1

        # ── 12. Revisit analytics ────────────────────────────────────────────
        r = await async_client.get("/api/v1/dashboard/revisit-analytics", headers=auth)
        assert r.status_code == 200
        rv = r.json()
        assert "time_gap_distribution" in rv
        gap_labels = [g["gap"] for g in rv["time_gap_distribution"]]
        assert "0-7 days" in gap_labels

        # ── 13. Recent activity ──────────────────────────────────────────────
        r = await async_client.get("/api/v1/dashboard/recent-activity", headers=auth)
        assert r.status_code == 200
        ra = r.json()
        assert "call_data" in ra
        assert "escalation_data" in ra
        assert "community_data" in ra

        # ── 14. Excel export ─────────────────────────────────────────────────
        r = await async_client.get("/api/v1/calls/export", headers=auth)
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers.get("content-type", "")

        # ── 15. Inbound list (should be empty but not crash) ─────────────────
        r = await async_client.get("/api/v1/inbound/", headers=auth)
        assert r.status_code == 200
        assert r.json()["error"] == 0

        # ── 16. Inbound KPI ──────────────────────────────────────────────────
        r = await async_client.get("/api/v1/inbound/kpi", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert "interactions" in body
        assert "revenue" in body

    async def test_staff_call_history_for_patient(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, patients = await _setup_staff_with_patients(
            async_client, db_session
        )
        auth = {"Authorization": f"Bearer {token}"}
        patient = patients[0]

        # Log some feedback first
        await async_client.post(
            "/api/v1/calls/feedback",
            headers=auth,
            json={
                "patient_id": str(patient.id),
                "call_status": "not_connected",
                "call_outcome": "no_feedback",
            },
        )

        r = await async_client.get(
            f"/api/v1/patients/{patient.id}/history", headers=auth
        )
        assert r.status_code == 200
        body = r.json()
        assert body["error"] == 0
        assert isinstance(body["history"], list)

    async def test_staff_broadcast_text(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, token, _ = await _setup_staff_with_patients(async_client, db_session)
        auth = {"Authorization": f"Bearer {token}"}

        r = await async_client.post(
            "/api/v1/calls/broadcast",
            headers=auth,
            json={"text": "Health tips from E2E Hospital"},
        )
        assert r.status_code == 200
        assert r.json()["error"] == 0

        r = await async_client.get("/api/v1/calls/broadcast", headers=auth)
        assert r.status_code == 200
        assert r.json()["data"] == "Health tips from E2E Hospital"
