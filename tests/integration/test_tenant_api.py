from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _create_hospital_with_config(async_client: AsyncClient, db_session: AsyncSession) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.organization import HospitalConfig, Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Branding Hospital {uuid.uuid4().hex[:6]}",
        slug=f"branding-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    cfg = HospitalConfig(
        id=uuid.uuid4(),
        organization_id=org.id,
        vobiz_did="+911234567890",
        branding_json={"logo_url": "https://example.com/logo.png", "primary_color": "#FF5733"},
        agent_prompt_prefix="You are an agent for Test Hospital.",
    )
    db_session.add(cfg)

    email = f"branding_{uuid.uuid4().hex[:8]}@hospital.com"
    password = "TestPass123!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        name="Branding User",
        role="hospital_user",
    )
    db_session.add(user)
    await db_session.flush()

    login = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["token"]
    return org.id, cfg.id, token


class TestTenantBranding:
    async def test_branding_returns_config(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        org_id, cfg_id, token = await _create_hospital_with_config(async_client, db_session)
        resp = await async_client.get(
            "/api/v1/tenant/branding",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["branding"]["primary_color"] == "#FF5733"
        assert body["branding"]["logo_url"] == "https://example.com/logo.png"
        assert "hospital_name" in body

    async def test_branding_unauthenticated(self, async_client: AsyncClient) -> None:
        resp = await async_client.get("/api/v1/tenant/branding")
        assert resp.status_code in (401, 403)

    async def test_branding_no_config_returns_defaults(self, async_client: AsyncClient, db_session: AsyncSession) -> None:
        from api.app.core.security import hash_password
        from api.app.models.auth import User
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name=f"No Config Hospital {uuid.uuid4().hex[:6]}",
            slug=f"noconfig-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        email = f"noconfig_{uuid.uuid4().hex[:8]}@hospital.com"
        password = "TestPass123!"
        db_session.add(User(
            id=uuid.uuid4(),
            organization_id=org.id,
            email=email,
            password_hash=hash_password(password),
            name="No Config User",
            role="hospital_user",
        ))
        await db_session.flush()

        login = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        token = login.json()["token"]

        resp = await async_client.get(
            "/api/v1/tenant/branding",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Should return empty branding dict, not 500
        assert isinstance(body["branding"], dict)
