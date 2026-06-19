from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_hospital_user(db_session: AsyncSession) -> tuple:
    """Seed an org + hospital_user for auth tests. Returns (org_id, user_id, email, password)."""
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.organization import Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Auth Test Hospital {uuid.uuid4().hex[:6]}",
        slug=f"auth-test-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    email = f"auth_{uuid.uuid4().hex[:8]}@hospital.com"
    password = "SecurePass123!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        name="Test User",
        role="hospital_user",
        perm_patient_engagement=True,
        perm_calllog_engagement=True,
    )
    db_session.add(user)
    await db_session.flush()
    return org.id, user.id, email, password


async def _seed_admin(db_session: AsyncSession) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.organization import Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Admin Test Org {uuid.uuid4().hex[:6]}",
        slug=f"admin-org-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    email = f"admin_{uuid.uuid4().hex[:8]}@fettle.com"
    password = "AdminPass456!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        name="Admin User",
        role="hospital_admin",
    )
    db_session.add(user)
    await db_session.flush()
    return org.id, user.id, email, password


class TestLoginEndpoint:
    async def test_login_hospital_user_success(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        org_id, user_id, email, password = await _seed_hospital_user(db_session)
        resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "token" in body
        assert len(body["token"]) > 20
        assert body["role"] == "hospital_user"

    async def test_login_admin_success(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        org_id, user_id, email, password = await _seed_admin(db_session)
        resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "token" in body
        assert body["role"] == "hospital_admin"

    async def test_login_wrong_password_fails(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        _, _, email, _ = await _seed_hospital_user(db_session)
        resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": "wrongpassword"})
        assert resp.status_code == 401

    async def test_login_unknown_user_fails(self, async_client: AsyncClient) -> None:
        resp = await async_client.post("/api/v1/auth/login", json={
            "email": "nobody@nowhere.com",
            "password": "anypassword",
        })
        assert resp.status_code == 401

    async def test_login_missing_email_fails(self, async_client: AsyncClient) -> None:
        resp = await async_client.post("/api/v1/auth/login", json={"password": "pw"})
        assert resp.status_code == 422

    async def test_login_returns_organization_id(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        org_id, _, email, password = await _seed_hospital_user(db_session)
        resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200
        body = resp.json()
        assert str(org_id) == body.get("organization_id")


class TestValidateToken:
    async def _get_token(self, async_client: AsyncClient, db_session: AsyncSession) -> str:
        _, _, email, password = await _seed_hospital_user(db_session)
        resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        return resp.json()["token"]

    async def test_validate_valid_token(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        token = await self._get_token(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "role" in body
        assert "name" in body

    async def test_validate_invalid_token(self, async_client: AsyncClient) -> None:
        resp = await async_client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "Bearer not.a.valid.token"},
        )
        assert resp.status_code == 401

    async def test_validate_missing_token(self, async_client: AsyncClient) -> None:
        resp = await async_client.get("/api/v1/auth/validate")
        assert resp.status_code in (401, 403)


class TestTabAccess:
    async def test_tab_access_returns_permissions(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        _, _, email, password = await _seed_hospital_user(db_session)
        login = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        token = login.json()["token"]
        resp = await async_client.get(
            "/api/v1/auth/tab-access",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "permissions" in body
        perms = body["permissions"]
        assert "patient_engagement" in perms
        assert perms["patient_engagement"] is True  # seeded with True
        assert perms["calllog_engagement"] is True
