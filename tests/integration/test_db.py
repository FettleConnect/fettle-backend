from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.integration


class TestDatabaseConnection:
    async def test_engine_ping(self, db_engine) -> None:
        async with db_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    async def test_session_fixture_works(self, db_session) -> None:
        result = await db_session.execute(text("SELECT 42 AS answer"))
        assert result.scalar() == 42


class TestOrganizationCRUD:
    async def test_create_organization(self, db_session) -> None:
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name="Test Hospital",
            slug=f"test-hospital-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        result = await db_session.execute(
            select(Organization).where(Organization.id == org.id)
        )
        fetched = result.scalar_one()
        assert fetched.name == "Test Hospital"
        assert fetched.type == "hospital"
        assert fetched.is_active is True

    async def test_organization_slug_unique(self, db_session) -> None:
        from sqlalchemy.exc import IntegrityError

        from api.app.models.organization import Organization

        slug = f"unique-slug-{uuid.uuid4().hex[:8]}"
        org1 = Organization(id=uuid.uuid4(), name="Org1", slug=slug, type="hospital")
        org2 = Organization(id=uuid.uuid4(), name="Org2", slug=slug, type="hospital")

        db_session.add(org1)
        await db_session.flush()
        db_session.add(org2)

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_hospital_config_cascade_delete(self, db_session) -> None:
        from api.app.models.organization import HospitalConfig, Organization

        org = Organization(
            id=uuid.uuid4(),
            name="Cascade Hospital",
            slug=f"cascade-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        cfg = HospitalConfig(
            id=uuid.uuid4(),
            organization_id=org.id,
            vobiz_did="+911234567890",
        )
        db_session.add(cfg)
        await db_session.flush()

        await db_session.delete(org)
        await db_session.flush()

        result = await db_session.execute(
            select(HospitalConfig).where(HospitalConfig.id == cfg.id)
        )
        assert result.scalar_one_or_none() is None


class TestUserCRUD:
    async def test_create_user(self, db_session) -> None:
        from api.app.models.auth import User
        from api.app.models.organization import Organization
        from api.app.core.security import hash_password

        org = Organization(
            id=uuid.uuid4(),
            name="User Test Hospital",
            slug=f"user-test-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        user = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email="admin@hospital.com",
            password_hash=hash_password("secret"),
            name="Admin User",
            role="hospital_admin",
        )
        db_session.add(user)
        await db_session.flush()

        result = await db_session.execute(
            select(User).where(User.id == user.id)
        )
        fetched = result.scalar_one()
        assert fetched.email == "admin@hospital.com"
        assert fetched.role == "hospital_admin"
        assert fetched.is_active is True

    async def test_user_unique_per_org_email(self, db_session) -> None:
        from sqlalchemy.exc import IntegrityError

        from api.app.models.auth import User
        from api.app.models.organization import Organization
        from api.app.core.security import hash_password

        org = Organization(
            id=uuid.uuid4(),
            name="Dup Test Org",
            slug=f"dup-org-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        u1 = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email="dup@test.com",
            password_hash=hash_password("pw"),
            name="User1",
            role="hospital_user",
        )
        u2 = User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email="dup@test.com",
            password_hash=hash_password("pw"),
            name="User2",
            role="hospital_user",
        )
        db_session.add(u1)
        await db_session.flush()
        db_session.add(u2)

        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestPatientCRUD:
    async def test_create_patient(self, db_session) -> None:
        from api.app.models.hospital.patient import Patient
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name="Patient Test Hospital",
            slug=f"patient-test-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        patient = Patient(
            id=uuid.uuid4(),
            organization_id=org.id,
            patient_name="John Doe",
            mobile_no="+919876543210",
            department="Cardiology",
            age=45,
        )
        db_session.add(patient)
        await db_session.flush()

        result = await db_session.execute(
            select(Patient).where(Patient.id == patient.id)
        )
        fetched = result.scalar_one()
        assert fetched.patient_name == "John Doe"
        assert fetched.department == "Cardiology"


class TestMultiTenantIsolation:
    async def test_two_orgs_can_have_same_patient_mobile(self, db_session) -> None:
        """Patients in different orgs can share a mobile number — isolation is via org."""
        from api.app.models.hospital.patient import Patient
        from api.app.models.organization import Organization

        org1 = Organization(
            id=uuid.uuid4(), name="Org1", slug=f"iso-org1-{uuid.uuid4().hex[:8]}", type="hospital"
        )
        org2 = Organization(
            id=uuid.uuid4(), name="Org2", slug=f"iso-org2-{uuid.uuid4().hex[:8]}", type="hospital"
        )
        db_session.add_all([org1, org2])
        await db_session.flush()

        p1 = Patient(
            id=uuid.uuid4(),
            organization_id=org1.id,
            patient_name="Jane",
            mobile_no="+910000000001",
            department="Ortho",
        )
        p2 = Patient(
            id=uuid.uuid4(),
            organization_id=org2.id,
            patient_name="Jane",
            mobile_no="+910000000001",
            department="Ortho",
        )
        db_session.add_all([p1, p2])
        # Should NOT raise — different orgs
        await db_session.flush()
