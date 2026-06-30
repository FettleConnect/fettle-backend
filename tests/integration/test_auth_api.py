from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_hospital_user(db_session: AsyncSession) -> tuple:
    from api.app.core.security import hash_password
    from api.app.models.auth import User
    from api.app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    subdomain = f"auth-{suffix}"
    org = Organization(
        id=uuid.uuid4(),
        name=f"Auth Test Hospital {uuid.uuid4().hex[:6]}",
        slug=f"auth-test-{suffix}",
        subdomain=subdomain,
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    email = f"auth_{suffix}@hospital.com"
    username = f"user_{suffix}"
    password = "SecurePass123!"
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        username=username,
        password_hash=hash_password(password),
        name="Test User",
        role="hospital_user",
        perm_patient_engagement=True,
        perm_calllog_engagement=True,
    )
    db_session.add(user)
    await db_session.flush()
    return org.id, user.id, email, password, subdomain, username


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
    async def test_login_hospital_user_success(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, email, password, _, _ = await _seed_hospital_user(db_session)
        resp = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "token" in body
        assert len(body["token"]) > 20
        assert body["role"] == "hospital_user"

    async def test_login_admin_success(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, email, password = await _seed_admin(db_session)
        resp = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == 0
        assert "token" in body
        assert body["role"] == "hospital_admin"

    async def test_login_wrong_password_fails(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, email, _, _, _ = await _seed_hospital_user(db_session)
        resp = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrongpassword"}
        )
        assert resp.status_code == 401

    async def test_username_login_uses_hospital_subdomain(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, _, _, password, subdomain, username = await _seed_hospital_user(
            db_session
        )
        resp = await async_client.post(
            "/api/v1/auth/login",
            headers={"X-Hospital-Subdomain": subdomain},
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "hospital_user"
        assert body["organization_id"] == str(org_id)

    async def test_username_login_requires_tenant(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, password, _, username = await _seed_hospital_user(db_session)
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 400

    async def test_admin_subdomain_is_not_a_hospital_tenant(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, _, password, _, username = await _seed_hospital_user(db_session)
        resp = await async_client.post(
            "/api/v1/auth/login",
            headers={"Host": "admin.example.com"},
            json={"username": username, "password": password},
        )
        assert resp.status_code == 400

    async def test_login_unknown_user_fails(self, async_client: AsyncClient) -> None:
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={
                "email": "nobody@nowhere.com",
                "password": "anypassword",
            },
        )
        assert resp.status_code == 401

    async def test_login_missing_email_fails(self, async_client: AsyncClient) -> None:
        resp = await async_client.post("/api/v1/auth/login", json={"password": "pw"})
        assert resp.status_code == 422

    async def test_login_returns_organization_id(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        org_id, _, email, password, _, _ = await _seed_hospital_user(db_session)
        resp = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert str(org_id) == body.get("organization_id")


class TestValidateToken:
    async def _get_token(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> str:
        _, _, email, password, _, _ = await _seed_hospital_user(db_session)
        resp = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        return resp.json()["token"]

    async def test_validate_valid_token(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
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
    async def test_tab_access_returns_permissions(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, _, email, password, _, _ = await _seed_hospital_user(db_session)
        login = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
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


class TestAdminConsole:
    async def _get_platform_admin_token(self, async_client: AsyncClient) -> str:
        resp = await async_client.post(
            "/admin/api/login",
            json={
                "username": "platform-admin",
                "password": "platform-admin-password",
            },
        )
        assert resp.status_code == 200
        return resp.json()["token"]

    async def test_admin_login_rejects_bad_env_credentials(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.post(
            "/admin/api/login",
            json={"username": "platform-admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_admin_page_renders(self, async_client: AsyncClient) -> None:
        resp = await async_client.get("/admin")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        assert "hospital-summary" in resp.text

    async def test_admin_login_page_renders(self, async_client: AsyncClient) -> None:
        resp = await async_client.get("/admin/login")
        assert resp.status_code == 200
        assert "Sign in" in resp.text

    async def test_admin_database_pages_render(self, async_client: AsyncClient) -> None:
        tables_resp = await async_client.get("/admin/tables")
        table_resp = await async_client.get("/admin/table?name=global_configs")
        hospitals_resp = await async_client.get("/admin/hospitals")
        hospital_resp = await async_client.get("/admin/hospital?id=test")
        corporate_resp = await async_client.get("/admin/corporate")
        assert tables_resp.status_code == 200
        assert "Database" in tables_resp.text
        assert table_resp.status_code == 200
        assert "Row editor" in table_resp.text
        assert hospitals_resp.status_code == 200
        assert "Hospital List" in hospitals_resp.text
        assert hospital_resp.status_code == 200
        assert "Change Password" in hospital_resp.text
        assert "hospital-table-stack" in hospital_resp.text
        assert "row-dialog" in hospital_resp.text
        assert corporate_resp.status_code == 200
        assert "Corporate Clients" in corporate_resp.text

    async def test_admin_subdomain_root_renders(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.get("/", headers={"Host": "admin.example.com"})
        assert resp.status_code == 200
        assert "Fettle Admin" in resp.text

    async def test_platform_admin_can_manage_hospitals_and_users(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await self._get_platform_admin_token(async_client)
        headers = {"Authorization": f"Bearer {token}"}
        suffix = uuid.uuid4().hex[:8]

        org_resp = await async_client.post(
            "/admin/api/organizations",
            headers=headers,
            json={
                "name": "ABC Hospital",
                "slug": f"abc-hospital-{suffix}",
                "subdomain": f"abc-{suffix}",
                "type": "hospital",
            },
        )
        assert org_resp.status_code == 201
        org = org_resp.json()
        assert org["surfaces"] == ["hospital", "transcript"]

        detail_with_default_admin = await async_client.get(
            f"/admin/api/hospitals/{org['id']}", headers=headers
        )
        assert detail_with_default_admin.status_code == 200
        assert any(
            item["username"] == "admin" and item["role"] == "hospital_admin"
            for item in detail_with_default_admin.json()["users"]
        )

        user_resp = await async_client.post(
            f"/admin/api/hospitals/{org['id']}/users",
            headers=headers,
            json={
                "username": f"abc_user_{suffix}",
                "name": "ABC User",
                "role": "hospital_user",
                "password": "HospitalUserPass123!",
                "perm_patient_engagement": True,
            },
        )
        assert user_resp.status_code == 201
        user = user_resp.json()
        assert user["organization_id"] == org["id"]
        assert user["username"] == f"abc_user_{suffix}"

        password_resp = await async_client.put(
            f"/admin/api/hospitals/{org['id']}/users/{user['id']}/password",
            headers=headers,
            json={"password": "NewHospitalUserPass123!"},
        )
        assert password_resp.status_code == 200

        detail_resp = await async_client.get(
            f"/admin/api/hospitals/{org['id']}", headers=headers
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["hospital"]["id"] == org["id"]
        assert any(item["id"] == user["id"] for item in detail_resp.json()["users"])

        hospitals_resp = await async_client.get("/admin/api/hospitals", headers=headers)
        assert hospitals_resp.status_code == 200
        assert any(item["id"] == org["id"] for item in hospitals_resp.json())

        metrics_resp = await async_client.get("/admin/api/metrics", headers=headers)
        assert metrics_resp.status_code == 200
        assert metrics_resp.json()["hospitals"] >= 1

        summary_resp = await async_client.get(
            "/admin/api/hospitals/summary", headers=headers
        )
        assert summary_resp.status_code == 200
        assert any(
            item["id"] == org["id"]
            and item["patient_count"] == 0
            and item["doctor_count"] == 0
            and item["user_count"] >= 1
            for item in summary_resp.json()
        )

    async def test_platform_admin_can_manage_database_rows(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await self._get_platform_admin_token(async_client)
        headers = {"Authorization": f"Bearer {token}"}
        key = f"admin-db-test-{uuid.uuid4().hex[:8]}"

        tables_resp = await async_client.get("/admin/api/db/tables", headers=headers)
        assert tables_resp.status_code == 200
        assert any(table["name"] == "global_configs" for table in tables_resp.json())

        create_resp = await async_client.post(
            "/admin/api/db/tables/global_configs/rows",
            headers=headers,
            json={"values": {"key": key, "value": "first"}},
        )
        assert create_resp.status_code == 201
        assert create_resp.json()["key"] == key

        update_resp = await async_client.put(
            "/admin/api/db/tables/global_configs/rows",
            headers=headers,
            json={"pk": {"key": key}, "values": {"value": "second"}},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["value"] == "second"

        rows_resp = await async_client.get(
            "/admin/api/db/tables/global_configs",
            headers=headers,
        )
        assert rows_resp.status_code == 200
        assert any(row["key"] == key for row in rows_resp.json()["rows"])

        delete_resp = await async_client.request(
            "DELETE",
            "/admin/api/db/tables/global_configs/rows",
            headers=headers,
            json={"pk": {"key": key}},
        )
        assert delete_resp.status_code == 204
