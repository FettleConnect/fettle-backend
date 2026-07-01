from __future__ import annotations

import hmac
import json
import secrets
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.schema import Column, Table

from api.app.core.config import settings
from api.app.core.security import (
    TokenError,
    create_admin_access_token,
    decode_admin_token,
    hash_password,
)
from api.app.db.base import Base
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.call import InboundCall, OutboundCall
from api.app.models.hospital.patient import Patient
from api.app.models.organization import CorporateConfig, HospitalConfig, Organization
from api.app.schemas.admin import (
    AdminDashboardMetrics,
    AdminDbColumn,
    AdminDbDelete,
    AdminDbMutation,
    AdminDbRows,
    AdminDbTable,
    AdminDbUpdate,
    AdminLoginRequest,
    AdminTokenResponse,
    HospitalAdminDetail,
    HospitalAdminSummary,
    HospitalUserAdminIn,
    OrganizationAdminIn,
    OrganizationAdminOut,
    UserAdminIn,
    UserAdminOut,
    UserPasswordAdminIn,
)
from api.app.services.tenant import is_admin_subdomain

router = APIRouter(prefix="/admin", tags=["admin"])
subdomain_router = APIRouter(tags=["admin"])
admin_bearer = HTTPBearer(auto_error=False)
_FRONTEND_DIR = Path(__file__).resolve().parent / "admin_frontend"
_ADMIN_INDEX = _FRONTEND_DIR / "index.html"
_ADMIN_LOGIN = _FRONTEND_DIR / "login.html"
_ADMIN_HOSPITALS = _FRONTEND_DIR / "hospitals.html"
_ADMIN_HOSPITAL = _FRONTEND_DIR / "hospital.html"
_ADMIN_CORPORATE = _FRONTEND_DIR / "corporate.html"
_ADMIN_TABLES = _FRONTEND_DIR / "tables.html"
_ADMIN_TABLE = _FRONTEND_DIR / "table.html"
_ADMIN_ASSETS = _FRONTEND_DIR / "assets"


class _PermissionInput(Protocol):
    perm_patient_engagement: bool
    perm_community_engagement: bool
    perm_revisit_engagement: bool
    perm_escalation_engagement: bool
    perm_calllog_engagement: bool
    perm_upload_engagement: bool
    perm_pdf_engagement: bool


async def get_current_platform_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(admin_bearer),
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_admin_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return str(payload.get("username") or "")


@router.get("")
async def admin_console() -> FileResponse:
    return FileResponse(_ADMIN_INDEX)


@router.get("/login")
async def admin_login_console() -> FileResponse:
    return FileResponse(_ADMIN_LOGIN)


@router.get("/hospitals")
async def admin_hospitals_console() -> FileResponse:
    return FileResponse(_ADMIN_HOSPITALS)


@router.get("/hospital")
async def admin_hospital_console() -> FileResponse:
    return FileResponse(_ADMIN_HOSPITAL)


@router.get("/corporate")
async def admin_corporate_console() -> FileResponse:
    return FileResponse(_ADMIN_CORPORATE)


@router.get("/tables")
async def admin_tables_console() -> FileResponse:
    return FileResponse(_ADMIN_TABLES)


@router.get("/table")
async def admin_table_console() -> FileResponse:
    return FileResponse(_ADMIN_TABLE)


@subdomain_router.get("/")
async def admin_subdomain_console(request: Request) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_ADMIN_INDEX)


@subdomain_router.get("/login")
async def admin_subdomain_login_console(request: Request) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_ADMIN_LOGIN)


@subdomain_router.get("/hospitals")
async def admin_subdomain_hospitals_console(request: Request) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_ADMIN_HOSPITALS)


@subdomain_router.get("/hospital")
async def admin_subdomain_hospital_console(request: Request) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_ADMIN_HOSPITAL)


@subdomain_router.get("/corporate")
async def admin_subdomain_corporate_console(request: Request) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_ADMIN_CORPORATE)


@subdomain_router.get("/tables")
async def admin_subdomain_tables_console(request: Request) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_ADMIN_TABLES)


@subdomain_router.get("/table")
async def admin_subdomain_table_console(request: Request) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_ADMIN_TABLE)


@router.get("/assets/{asset_path:path}")
async def admin_asset(asset_path: str) -> FileResponse:
    asset = (_ADMIN_ASSETS / asset_path).resolve()
    if not asset.is_file() or _ADMIN_ASSETS.resolve() not in asset.parents:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(asset)


@subdomain_router.get("/assets/{asset_path:path}")
async def admin_subdomain_asset(request: Request, asset_path: str) -> FileResponse:
    if not is_admin_subdomain(request):
        raise HTTPException(status_code=404, detail="Not found")
    return await admin_asset(asset_path)


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


@router.get("/api/metrics", response_model=AdminDashboardMetrics)
async def dashboard_metrics(
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> AdminDashboardMetrics:
    return AdminDashboardMetrics(
        organizations=await _count(
            session, select(func.count()).select_from(Organization)
        ),
        hospitals=await _count(
            session,
            select(func.count())
            .select_from(Organization)
            .where(Organization.type == "hospital"),
        ),
        corporate_clients=await _count(
            session,
            select(func.count())
            .select_from(Organization)
            .where(Organization.type == "corporate"),
        ),
        users=await _count(session, select(func.count()).select_from(User)),
        doctors=await _count(
            session, select(func.count()).select_from(User).where(User.role == "doctor")
        ),
        patients=await _count(session, select(func.count()).select_from(Patient)),
        inbound_calls=await _count(
            session, select(func.count()).select_from(InboundCall)
        ),
        outbound_calls=await _count(
            session, select(func.count()).select_from(OutboundCall)
        ),
    )


@router.get("/api/hospitals", response_model=list[OrganizationAdminOut])
async def list_hospitals(
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> list[OrganizationAdminOut]:
    return await _list_organizations(session, org_type="hospital")


@router.get("/api/hospitals/summary", response_model=list[HospitalAdminSummary])
async def list_hospital_summaries(
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> list[HospitalAdminSummary]:
    return await _hospital_summaries(session)


@router.get("/api/hospitals/{hospital_id}", response_model=HospitalAdminDetail)
async def get_hospital_detail(
    hospital_id: uuid.UUID,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> HospitalAdminDetail:
    hospital = await _hospital_summary(session, hospital_id)
    users = list(
        (
            await session.scalars(
                select(User)
                .where(User.organization_id == hospital_id)
                .order_by(User.name.asc())
            )
        ).all()
    )
    return HospitalAdminDetail(
        hospital=hospital,
        users=[_user_payload(user) for user in users],
    )


@router.post(
    "/api/hospitals/{hospital_id}/users",
    response_model=UserAdminOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_hospital_user(
    hospital_id: uuid.UUID,
    body: HospitalUserAdminIn,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> UserAdminOut:
    await _require_hospital(session, hospital_id)
    if not body.password:
        raise HTTPException(status_code=422, detail="password is required")
    user = User(
        organization_id=hospital_id,
        email=body.email or _default_user_email(hospital_id, body.username or "user"),
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


@router.put(
    "/api/hospitals/{hospital_id}/users/{user_id}",
    response_model=UserAdminOut,
)
async def update_hospital_user(
    hospital_id: uuid.UUID,
    user_id: uuid.UUID,
    body: HospitalUserAdminIn,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> UserAdminOut:
    await _require_hospital(session, hospital_id)
    user = await _require_hospital_user(session, hospital_id, user_id)
    user.email = body.email or _default_user_email(hospital_id, body.username or "user")
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


@router.put(
    "/api/hospitals/{hospital_id}/users/{user_id}/password",
    response_model=UserAdminOut,
)
async def change_hospital_user_password(
    hospital_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UserPasswordAdminIn,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> UserAdminOut:
    await _require_hospital(session, hospital_id)
    user = await _require_hospital_user(session, hospital_id, user_id)
    user.password_hash = hash_password(body.password)
    await session.commit()
    await session.refresh(user)
    return _user_payload(user)


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
    await _ensure_surface_config(session, org)
    if org.type == "hospital":
        await _ensure_default_hospital_admin(session, org)
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
    await _ensure_surface_config(session, org)
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
        email=body.email or _default_user_email(org_id, body.username or "user"),
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
    organization_id = _parse_org_id(body.organization_id)
    user.organization_id = organization_id
    user.email = body.email or _default_user_email(
        organization_id, body.username or "user"
    )
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


@router.get("/api/db/tables", response_model=list[AdminDbTable])
async def list_db_tables(
    current_admin: str = Depends(get_current_platform_admin),
) -> list[AdminDbTable]:
    return [_table_payload(table) for table in _all_admin_tables()]


@router.get("/api/db/tables/{table_name}", response_model=AdminDbRows)
async def list_db_rows(
    table_name: str,
    limit: int = 100,
    offset: int = 0,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> AdminDbRows:
    table = _get_admin_table(table_name)
    safe_limit = min(max(limit, 1), 500)
    safe_offset = max(offset, 0)
    order_by = list(table.primary_key.columns) or list(table.columns)[:1]
    result = await session.execute(
        select(table).order_by(*order_by).limit(safe_limit).offset(safe_offset)
    )
    rows = [_serialize_row(row._mapping) for row in result.fetchall()]
    return AdminDbRows(
        table=_table_payload(table),
        rows=rows,
        limit=safe_limit,
        offset=safe_offset,
    )


@router.post(
    "/api/db/tables/{table_name}/rows",
    response_model=dict[str, object | None],
    status_code=status.HTTP_201_CREATED,
)
async def create_db_row(
    table_name: str,
    body: AdminDbMutation,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, object | None]:
    table = _get_admin_table(table_name)
    values = _coerce_values(table, body.values)
    stmt = insert(table).values(**values).returning(table)
    row = (await session.execute(stmt)).mappings().one()
    await session.commit()
    return _serialize_row(row)


@router.put("/api/db/tables/{table_name}/rows", response_model=dict[str, object | None])
async def update_db_row(
    table_name: str,
    body: AdminDbUpdate,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, object | None]:
    table = _get_admin_table(table_name)
    values = _coerce_values(table, body.values)
    if not values:
        raise HTTPException(status_code=422, detail="No values supplied")
    stmt = (
        update(table)
        .where(*_primary_key_filters(table, body.pk))
        .values(**values)
        .returning(table)
    )
    row = (await session.execute(stmt)).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found")
    await session.commit()
    return _serialize_row(row)


@router.delete(
    "/api/db/tables/{table_name}/rows", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_db_row(
    table_name: str,
    body: AdminDbDelete,
    current_admin: str = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    table = _get_admin_table(table_name)
    result = cast(
        CursorResult[Any],
        await session.execute(
            delete(table).where(*_primary_key_filters(table, body.pk))
        ),
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Row not found")
    await session.commit()


async def _list_organizations(
    session: AsyncSession, org_type: str | None = None
) -> list[OrganizationAdminOut]:
    stmt = select(Organization).order_by(Organization.name.asc())
    if org_type is not None:
        stmt = stmt.where(Organization.type == org_type)
    orgs = list((await session.scalars(stmt)).all())
    return [_organization_payload(org) for org in orgs]


async def _hospital_summary(
    session: AsyncSession, hospital_id: uuid.UUID
) -> HospitalAdminSummary:
    summaries = await _hospital_summaries(session, hospital_id=hospital_id)
    if not summaries:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return summaries[0]


async def _hospital_summaries(
    session: AsyncSession, hospital_id: uuid.UUID | None = None
) -> list[HospitalAdminSummary]:
    patient_counts = _count_subquery(Patient, "patients")
    doctor_counts = (
        select(User.organization_id, func.count(User.id).label("doctors"))
        .where(User.role == "doctor")
        .group_by(User.organization_id)
        .subquery()
    )
    user_counts = _count_subquery(User, "users")
    inbound_counts = _count_subquery(InboundCall, "inbound_calls")
    outbound_counts = _count_subquery(OutboundCall, "outbound_calls")
    stmt = (
        select(
            Organization,
            func.coalesce(patient_counts.c.patients, 0).label("patient_count"),
            func.coalesce(doctor_counts.c.doctors, 0).label("doctor_count"),
            func.coalesce(user_counts.c.users, 0).label("user_count"),
            func.coalesce(inbound_counts.c.inbound_calls, 0).label(
                "inbound_call_count"
            ),
            func.coalesce(outbound_counts.c.outbound_calls, 0).label(
                "outbound_call_count"
            ),
        )
        .outerjoin(patient_counts, patient_counts.c.organization_id == Organization.id)
        .outerjoin(doctor_counts, doctor_counts.c.organization_id == Organization.id)
        .outerjoin(user_counts, user_counts.c.organization_id == Organization.id)
        .outerjoin(inbound_counts, inbound_counts.c.organization_id == Organization.id)
        .outerjoin(
            outbound_counts, outbound_counts.c.organization_id == Organization.id
        )
        .where(Organization.type == "hospital")
        .order_by(Organization.name.asc())
    )
    if hospital_id is not None:
        stmt = stmt.where(Organization.id == hospital_id)
    rows = (await session.execute(stmt)).all()
    return [
        HospitalAdminSummary(
            id=str(org.id),
            name=org.name,
            slug=org.slug,
            subdomain=org.subdomain,
            is_active=org.is_active,
            patient_count=int(patient_count),
            doctor_count=int(doctor_count),
            user_count=int(user_count),
            inbound_call_count=int(inbound_call_count),
            outbound_call_count=int(outbound_call_count),
        )
        for (
            org,
            patient_count,
            doctor_count,
            user_count,
            inbound_call_count,
            outbound_call_count,
        ) in rows
    ]


async def _require_hospital(
    session: AsyncSession, hospital_id: uuid.UUID
) -> Organization:
    hospital = await session.get(Organization, hospital_id)
    if hospital is None or hospital.type != "hospital":
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _require_hospital_user(
    session: AsyncSession, hospital_id: uuid.UUID, user_id: uuid.UUID
) -> User:
    user = await session.get(User, user_id)
    if user is None or user.organization_id != hospital_id:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _count(session: AsyncSession, stmt: Any) -> int:
    return int(await session.scalar(stmt) or 0)


def _count_subquery(model: Any, label: str) -> Any:
    return (
        select(model.organization_id, func.count(model.id).label(label))
        .group_by(model.organization_id)
        .subquery()
    )


async def _ensure_default_hospital_admin(
    session: AsyncSession, org: Organization
) -> None:
    existing_admin = await session.scalar(
        select(User.id).where(
            User.organization_id == org.id,
            User.username == "admin",
        )
    )
    if existing_admin is not None:
        return
    session.add(
        User(
            organization_id=org.id,
            email=_default_user_email(org.id, "admin"),
            username="admin",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            name="Admin",
            role="hospital_admin",
            is_active=True,
            perm_patient_engagement=True,
            perm_community_engagement=True,
            perm_revisit_engagement=True,
            perm_escalation_engagement=True,
            perm_calllog_engagement=True,
            perm_upload_engagement=True,
            perm_pdf_engagement=True,
        )
    )


def _default_user_email(organization_id: uuid.UUID, username: str) -> str:
    safe_username = username.strip().lower() or "user"
    return f"{safe_username}-{uuid.uuid4().hex[:8]}-{organization_id}@local.fettle"


async def _ensure_surface_config(session: AsyncSession, org: Organization) -> None:
    if org.type == "hospital":
        existing_hospital_config = await session.scalar(
            select(HospitalConfig.id).where(HospitalConfig.organization_id == org.id)
        )
        if existing_hospital_config is None:
            session.add(HospitalConfig(organization_id=org.id))
    if org.type == "corporate":
        existing_corporate_config = await session.scalar(
            select(CorporateConfig.id).where(CorporateConfig.organization_id == org.id)
        )
        if existing_corporate_config is None:
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


def _apply_permissions(user: User, body: _PermissionInput) -> None:
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


def _all_admin_tables() -> list[Table]:
    return sorted(Base.metadata.sorted_tables, key=lambda table: table.name)


def _get_admin_table(table_name: str) -> Table:
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return table


def _table_payload(table: Table) -> AdminDbTable:
    return AdminDbTable(
        name=table.name,
        columns=[
            AdminDbColumn(
                name=column.name,
                type=str(column.type),
                primary_key=column.primary_key,
                nullable=bool(column.nullable),
                default=_column_default(column),
            )
            for column in table.columns
        ],
        primary_key=[column.name for column in table.primary_key.columns],
    )


def _primary_key_filters(table: Table, values: dict[str, object | None]) -> list[Any]:
    filters = []
    for column in table.primary_key.columns:
        if column.name not in values:
            raise HTTPException(
                status_code=422,
                detail=f"Missing primary key value for {column.name}",
            )
        filters.append(column == _coerce_value(column, values[column.name]))
    if not filters:
        raise HTTPException(status_code=422, detail="Table has no primary key")
    return filters


def _coerce_values(table: Table, values: dict[str, object | None]) -> dict[str, Any]:
    coerced = {}
    for name, value in values.items():
        column = table.columns.get(name)
        if column is None:
            raise HTTPException(status_code=422, detail=f"Unknown column: {name}")
        coerced[name] = _coerce_value(column, value)
    return coerced


def _coerce_value(column: Column[Any], value: object | None) -> Any:
    if value is None or value == "":
        return None
    python_type = _column_python_type(column)
    if python_type is None:
        return value
    if python_type is uuid.UUID:
        return uuid.UUID(str(value))
    if python_type is datetime:
        return datetime.fromisoformat(str(value))
    if python_type is date:
        return date.fromisoformat(str(value))
    if python_type is time:
        return time.fromisoformat(str(value))
    if python_type is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if python_type is int:
        return int(cast(Any, value))
    if python_type is float:
        return float(cast(Any, value))
    if python_type is Decimal:
        return Decimal(str(value))
    if python_type in {dict, list} and isinstance(value, str):
        return json.loads(value)
    return value


def _column_python_type(column: Column[Any]) -> type[Any] | None:
    try:
        return column.type.python_type
    except NotImplementedError:
        return None


def _column_default(column: Column[Any]) -> str | None:
    default = column.default
    if default is None:
        return None
    default_arg = getattr(default, "arg", None)
    return str(default_arg) if default_arg is not None else None


def _serialize_row(row: RowMapping) -> dict[str, object | None]:
    return {key: _serialize_value(value) for key, value in row.items()}


def _serialize_value(value: object | None) -> object | None:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
