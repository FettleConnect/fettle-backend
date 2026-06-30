from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.config import settings
from api.app.core.security import (
    TokenError,
    create_admin_access_token,
    decode_admin_token,
    hash_password,
)
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.organization import CorporateConfig, HospitalConfig, Organization
from api.app.schemas.admin import (
    AdminLoginRequest,
    AdminTokenResponse,
    OrganizationAdminIn,
    OrganizationAdminOut,
    UserAdminIn,
    UserAdminOut,
)
from api.app.services.tenant import is_admin_subdomain

router = APIRouter(prefix="/admin", tags=["admin"])
subdomain_router = APIRouter(tags=["admin"])
admin_bearer = HTTPBearer()


async def get_current_platform_admin(
    credentials: HTTPAuthorizationCredentials = Depends(admin_bearer),
) -> str:
    try:
        payload = decode_admin_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return str(payload.get("username") or "")


@router.get("", response_class=HTMLResponse)
async def admin_console() -> HTMLResponse:
    return HTMLResponse(_ADMIN_HTML)


@subdomain_router.get("/", response_class=HTMLResponse)
async def admin_subdomain_console(request: Request) -> HTMLResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return HTMLResponse(_ADMIN_HTML)


@router.post("/api/login", response_model=AdminTokenResponse)
async def login_admin(body: AdminLoginRequest) -> AdminTokenResponse:
    if not settings.admin_username or not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin credentials are not configured",
        )

    configured_username = settings.admin_username.strip().lower()
    configured_password = settings.admin_password
    username_matches = hmac.compare_digest(body.username, configured_username)
    password_matches = hmac.compare_digest(body.password, configured_password)
    if not username_matches or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AdminTokenResponse(
        token=create_admin_access_token(username=configured_username)
    )


@router.get("/api/hospitals", response_model=list[OrganizationAdminOut])
async def list_hospitals(
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> list[OrganizationAdminOut]:
    return await _list_organizations(session, org_type="hospital")


@router.get("/api/organizations", response_model=list[OrganizationAdminOut])
async def list_organizations(
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> list[OrganizationAdminOut]:
    return await _list_organizations(session)


@router.post(
    "/api/organizations",
    response_model=OrganizationAdminOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    body: OrganizationAdminIn,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> OrganizationAdminOut:
    conflicts = [Organization.slug == body.slug]
    if body.subdomain:
        conflicts.append(Organization.subdomain == body.subdomain)
    existing = await session.scalar(select(Organization).where(or_(*conflicts)))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Organization already exists")

    org = Organization(
        name=body.name,
        slug=body.slug,
        type=body.type,
        subdomain=body.subdomain,
        is_active=body.is_active,
    )
    session.add(org)
    await session.flush()
    _ensure_surface_config(session, org)
    await session.commit()
    await session.refresh(org)
    return _organization_payload(org)


@router.put("/api/organizations/{organization_id}", response_model=OrganizationAdminOut)
async def update_organization(
    organization_id: uuid.UUID,
    body: OrganizationAdminIn,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> OrganizationAdminOut:
    org = await session.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.name = body.name
    org.slug = body.slug
    org.type = body.type
    org.subdomain = body.subdomain
    org.is_active = body.is_active
    _ensure_surface_config(session, org)
    await session.commit()
    await session.refresh(org)
    return _organization_payload(org)


@router.get("/api/users", response_model=list[UserAdminOut])
async def list_users(
    organization_id: uuid.UUID | None = None,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> list[UserAdminOut]:
    stmt = select(User)
    if organization_id is not None:
        stmt = stmt.where(User.organization_id == organization_id)
    users = list((await session.scalars(stmt.order_by(User.name.asc()))).all())
    return [_user_payload(user) for user in users]


@router.post(
    "/api/users", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED
)
async def create_user(
    body: UserAdminIn,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> UserAdminOut:
    if not body.password:
        raise HTTPException(status_code=422, detail="password is required")
    org_id = _parse_org_id(body.organization_id)
    if await session.get(Organization, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    user = User(
        organization_id=org_id,
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.name,
        role=body.role,
        is_active=body.is_active,
    )
    _apply_permissions(user, body)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _user_payload(user)


@router.put("/api/users/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserAdminIn,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> UserAdminOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.organization_id = _parse_org_id(body.organization_id)
    user.email = body.email
    user.username = body.username
    user.name = body.name
    user.role = body.role
    user.is_active = body.is_active
    if body.password:
        user.password_hash = hash_password(body.password)
    _apply_permissions(user, body)
    await session.commit()
    await session.refresh(user)
    return _user_payload(user)


async def _list_organizations(
    session: AsyncSession, org_type: str | None = None
) -> list[OrganizationAdminOut]:
    stmt = select(Organization).order_by(Organization.name.asc())
    if org_type is not None:
        stmt = stmt.where(Organization.type == org_type)
    orgs = list((await session.scalars(stmt)).all())
    return [_organization_payload(org) for org in orgs]


def _ensure_surface_config(session: AsyncSession, org: Organization) -> None:
    if org.type in {"hospital", "clinical"} and org.hospital_config is None:
        session.add(HospitalConfig(organization_id=org.id))
    if org.type == "corporate" and org.corporate_config is None:
        session.add(CorporateConfig(organization_id=org.id))


def _organization_payload(org: Organization) -> OrganizationAdminOut:
    return OrganizationAdminOut(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        type=org.type,
        subdomain=org.subdomain,
        is_active=org.is_active,
        surfaces=_surfaces_for_org(org),
    )


def _surfaces_for_org(org: Organization) -> list[str]:
    if org.type == "hospital":
        return ["hospital", "transcript"]
    if org.type == "corporate":
        return ["corporate"]
    if org.type == "clinical":
        return ["transcript"]
    return []


def _user_payload(user: User) -> UserAdminOut:
    return UserAdminOut(
        id=str(user.id),
        organization_id=str(user.organization_id),
        email=user.email,
        username=user.username,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        permissions={
            "patient_engagement": bool(user.perm_patient_engagement),
            "community_engagement": bool(user.perm_community_engagement),
            "revisit_engagement": bool(user.perm_revisit_engagement),
            "escalation_engagement": bool(user.perm_escalation_engagement),
            "calllog_engagement": bool(user.perm_calllog_engagement),
            "upload_engagement": bool(user.perm_upload_engagement),
            "pdf_engagement": bool(user.perm_pdf_engagement),
        },
    )


def _apply_permissions(user: User, body: UserAdminIn) -> None:
    user.perm_patient_engagement = body.perm_patient_engagement
    user.perm_community_engagement = body.perm_community_engagement
    user.perm_revisit_engagement = body.perm_revisit_engagement
    user.perm_escalation_engagement = body.perm_escalation_engagement
    user.perm_calllog_engagement = body.perm_calllog_engagement
    user.perm_upload_engagement = body.perm_upload_engagement
    user.perm_pdf_engagement = body.perm_pdf_engagement


def _parse_org_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid organization_id") from exc


_ADMIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fettle Admin</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; background: #f7f7f8; color: #18181b; }
    main { max-width: 1180px; margin: 0 auto; padding: 24px; }
    header { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
    input, select, button { padding: 9px 10px; border: 1px solid #d4d4d8; border-radius: 6px; }
    button { background: #155eef; color: white; border-color: #155eef; cursor: pointer; }
    section { margin-top: 20px; background: white; border: 1px solid #e4e4e7; border-radius: 8px; padding: 16px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #ececef; vertical-align: top; }
    form { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .wide { grid-column: span 2; }
    .muted { color: #71717a; }
  </style>
</head>
<body>
<main>
  <header>
    <h1>Fettle Admin</h1>
  </header>
  <section id="login-section">
    <h2>Sign in</h2>
    <form id="login-form">
      <input name="username" placeholder="Username" autocomplete="username" required />
      <input name="password" placeholder="Password" type="password" autocomplete="current-password" required />
      <button>Sign in</button>
    </form>
  </section>
  <section>
    <h2>Organizations</h2>
    <form id="org-form">
      <input name="name" placeholder="Name" required />
      <input name="slug" placeholder="slug" required />
      <input name="subdomain" placeholder="subdomain" />
      <select name="type"><option>hospital</option><option>corporate</option><option>clinical</option></select>
      <button>Create organization</button>
    </form>
    <table id="orgs"><thead><tr><th>Name</th><th>Subdomain</th><th>Type</th><th>Surfaces</th></tr></thead><tbody></tbody></table>
  </section>
  <section>
    <h2>Users</h2>
    <form id="user-form">
      <input name="organization_id" class="wide" placeholder="Organization ID" required />
      <input name="name" placeholder="Name" required />
      <input name="email" placeholder="Email" required />
      <input name="username" placeholder="Username" />
      <input name="password" placeholder="Password" type="password" required />
      <select name="role"><option>hospital_admin</option><option>hospital_user</option><option>doctor</option><option>corporate_admin</option><option>hr</option><option>employee</option><option>superadmin</option></select>
      <button>Create user</button>
    </form>
    <table id="users"><thead><tr><th>Name</th><th>Email</th><th>Username</th><th>Role</th><th>Organization</th></tr></thead><tbody></tbody></table>
  </section>
</main>
<script>
let adminToken = window.sessionStorage.getItem('fettle_admin_token') || '';
const token = () => adminToken;
const headers = () => ({'Content-Type': 'application/json', 'Authorization': `Bearer ${token()}`});
async function api(path, opts = {}) {
  const res = await fetch(path, {...opts, headers: {...headers(), ...(opts.headers || {})}});
  if (!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : res.json();
}
async function refresh() {
  if (!token()) return;
  const [orgs, users] = await Promise.all([api('/admin/api/organizations'), api('/admin/api/users')]);
  document.querySelector('#orgs tbody').innerHTML = orgs.map(o => `<tr><td>${o.name}<div class="muted">${o.id}</div></td><td>${o.subdomain || ''}</td><td>${o.type}</td><td>${o.surfaces.join(', ')}</td></tr>`).join('');
  document.querySelector('#users tbody').innerHTML = users.map(u => `<tr><td>${u.name}</td><td>${u.email}</td><td>${u.username || ''}</td><td>${u.role}</td><td>${u.organization_id}</td></tr>`).join('');
}
document.querySelector('#login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  const res = await fetch('/admin/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!res.ok) throw new Error(await res.text());
  adminToken = (await res.json()).token;
  window.sessionStorage.setItem('fettle_admin_token', adminToken);
  e.target.reset();
  refresh();
});
document.querySelector('#org-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  await api('/admin/api/organizations', {method: 'POST', body: JSON.stringify(body)});
  e.target.reset();
  refresh();
});
document.querySelector('#user-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  await api('/admin/api/users', {method: 'POST', body: JSON.stringify(body)});
  e.target.reset();
  refresh();
});
refresh();
</script>
</body>
</html>
"""
