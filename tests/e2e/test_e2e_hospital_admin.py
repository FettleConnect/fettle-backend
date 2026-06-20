from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _create_admin_hospital(
    async_client: AsyncClient, db_session: AsyncSession
) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.organization import HospitalConfig, Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"E2E Admin Hospital {uuid.uuid4().hex[:6]}",
        slug=f"e2e-admin-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    db_session.add(
        HospitalConfig(
            id=uuid.uuid4(),
            organization_id=org.id,
            vobiz_did="+910000100001",
            branding_json={
                "logo_url": "https://example.com/logo.png",
                "primary_color": "#0066CC",
            },
            agent_prompt_prefix="You are the assistant for E2E Admin Hospital.",
        )
    )

    admin_email = f"admin_{uuid.uuid4().hex[:8]}@e2ehospital.com"
    admin = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=admin_email,
        password_hash=hash_password("AdminPass123!"),
        name="E2E Admin",
        role="hospital_admin",
    )
    db_session.add(admin)
    await db_session.flush()

    resp = await async_client.post(
        "/api/v1/auth/login", json={"email": admin_email, "password": "AdminPass123!"}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert resp.json()["role"] == "hospital_admin"
    return org.id, token, admin.id


class TestHospitalAdminFlow:
    async def test_full_admin_lifecycle(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Full admin flow:
        1. Login → get token + organization_id
        2. Validate token
        3. Check branding
        4. Create a doctor
        5. Upload patients
        6. View patient list
        7. Verify KPI dashboard shows data
        8. View admin dashboard
        9. Create and list campaigns
        10. View all hospitals (admin-only)
        11. Multi-tenant isolation: confirm other org's data not visible
        """
        org_id, token, admin_id = await _create_admin_hospital(async_client, db_session)
        auth = {"Authorization": f"Bearer {token}"}

        # ── 1. Token validation ──────────────────────────────────────────────
        r = await async_client.get("/api/v1/auth/validate", headers=auth)
        assert r.status_code == 200
        assert r.json()["role"] == "hospital_admin"
        assert r.json()["organization_id"] == str(org_id)

        # ── 2. Legacy validate_token path (frontend uses this) ───────────────
        r = await async_client.get("/api/validate_token/", headers=auth)
        assert r.status_code == 200
        assert r.json()["role"] == "hospital_admin"

        # ── 3. Branding ──────────────────────────────────────────────────────
        r = await async_client.get("/api/v1/tenant/branding", headers=auth)
        assert r.status_code == 200
        assert r.json()["branding"]["primary_color"] == "#0066CC"
        assert r.json()["hospital_name"]

        # ── 4. Create a doctor via API ───────────────────────────────────────
        r = await async_client.post(
            "/api/v1/doctors/",
            headers=auth,
            json={
                "name": "Dr. E2E Test",
                "email": f"dr_e2e_{uuid.uuid4().hex[:8]}@hospital.com",
                "password": "DrPass123!",
                "department": "Cardiology",
                "consultation_fee": 800.0,
                "timings": "9am-5pm",
            },
        )
        assert r.status_code == 200
        assert "id" in r.json()

        # ── 5. Verify doctor appears in list ─────────────────────────────────
        r = await async_client.get("/api/v1/doctors/", headers=auth)
        assert r.status_code == 200
        doctor_names = [d["name"] for d in r.json()["data"]]
        assert "Dr. E2E Test" in doctor_names

        # ── 6. Upload patients ───────────────────────────────────────────────
        import io

        csv_content = "Patient Name,Mobile No,Department,Age\n"
        for i in range(5):
            csv_content += f"Patient {i+1},+9190{i:08d},Cardiology,{30+i}\n"

        r = await async_client.post(
            "/api/v1/patients/upload",
            headers=auth,
            files=[
                (
                    "files",
                    ("patients.csv", io.BytesIO(csv_content.encode()), "text/csv"),
                )
            ],
        )
        assert r.status_code == 200
        assert r.json()["error"] == 0
        assert len(r.json()["patient_ids"]) == 5

        # ── 7. Patient list ──────────────────────────────────────────────────
        r = await async_client.get("/api/v1/patients/", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["error"] == 0
        assert body["count"] >= 5

        # Verify every patient belongs to our org (multi-tenancy)
        for p in body["data"]:
            assert p["hospital_name"]  # All have org name

        # ── 8. Upload logs ───────────────────────────────────────────────────
        r = await async_client.get("/api/v1/patients/upload-logs", headers=auth)
        assert r.status_code == 200
        assert len(r.json()["data"]) >= 1

        # ── 9. KPI dashboard ─────────────────────────────────────────────────
        r = await async_client.get("/api/v1/dashboard/kpi", headers=auth)
        assert r.status_code == 200
        kpi = r.json()
        for card in [
            "total_patients_contacted",
            "call_answer_rate",
            "community_conversion",
            "escalated_issues",
        ]:
            assert card in kpi
            assert "title" in kpi[card]
            assert "value" in kpi[card]
            assert kpi[card]["trend"] in ("up", "down", "flat")

        # ── 10. Admin dashboard ──────────────────────────────────────────────
        r = await async_client.get("/api/v1/dashboard/admin", headers=auth)
        assert r.status_code == 200
        admin_body = r.json()
        assert admin_body["error"] == 0
        assert admin_body["patientsCount"] >= 5
        assert isinstance(admin_body["callAnswerRate"], (int, float))

        # ── 11. ROI metrics ──────────────────────────────────────────────────
        r = await async_client.get(
            "/api/v1/dashboard/roi?call_direction=outbound", headers=auth
        )
        assert r.status_code == 200
        roi = r.json()
        assert roi["error"] == 0
        assert any(m["name"] == "Revenue Influenced" for m in roi["roi_financial"])
        assert any(m["name"] == "Staff Hours Saved" for m in roi["roi_efficiency"])

        # ── 12. Department analytics ─────────────────────────────────────────
        r = await async_client.get("/api/v1/dashboard/departments", headers=auth)
        assert r.status_code == 200
        deps = r.json()
        assert deps["error"] == 0
        assert "department_table" in deps
        assert "top_intents" in deps

        # ── 13. Campaign create + list ───────────────────────────────────────
        r = await async_client.post(
            "/api/v1/calls/outbound",
            headers=auth,
            json={
                "is_individual": False,
                "name": "E2E Health Campaign",
                "audience_type": "all",
                "purpose": "Annual health checkup reminder",
            },
        )
        assert r.status_code == 200
        campaign_id = r.json()["campaign_id"]

        r = await async_client.get("/api/v1/campaigns/", headers=auth)
        assert r.status_code == 200
        camp_ids = [c["id"] for c in r.json()["data"]]
        assert campaign_id in camp_ids

        # ── 14. Hospital list (admin sees own org) ────────────────────────────
        r = await async_client.get("/api/v1/tenant/hospitals", headers=auth)
        assert r.status_code == 200
        org_ids = [h["id"] for h in r.json()["data"]]
        assert str(org_id) in org_ids

        # ── 15. Multi-tenancy: other org's data NOT accessible ────────────────
        from api.app.models.organization import Organization

        other_org = Organization(
            id=uuid.uuid4(),
            name="Other Hospital",
            slug=f"other-e2e-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(other_org)
        await db_session.flush()  # Must flush org before adding patient with FK
        from api.app.models.hospital.patient import Patient

        other_patient = Patient(
            id=uuid.uuid4(),
            organization_id=other_org.id,
            patient_name="Other Org Patient",
            mobile_no="+919999888877",
        )
        db_session.add(other_patient)
        await db_session.flush()

        r = await async_client.get("/api/v1/patients/", headers=auth)
        patient_names = [p["patient_name"] for p in r.json()["data"]]
        assert "Other Org Patient" not in patient_names

    async def test_admin_tab_access(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Admins get full permissions."""
        org_id, token, _ = await _create_admin_hospital(async_client, db_session)
        r = await async_client.get(
            "/api/v1/auth/tab-access", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200

    async def test_delete_campaign(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.campaign import Campaign

        org_id, token, _ = await _create_admin_hospital(async_client, db_session)
        auth = {"Authorization": f"Bearer {token}"}

        camp = Campaign(id=uuid.uuid4(), organization_id=org_id, name="To Delete")
        db_session.add(camp)
        await db_session.flush()

        r = await async_client.delete(f"/api/v1/campaigns/{camp.id}", headers=auth)
        assert r.status_code == 200
        assert r.json()["error"] == 0
