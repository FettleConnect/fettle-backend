from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.organization import HospitalConfig, Organization
from api.app.schemas.tenant import BrandingResponse
from api.app.views.deps import get_optional_current_user

router = APIRouter(prefix="/tenant", tags=["tenant"])


def _verify_internal_secret(request: Request) -> None:
    """Validate X-Internal-API-Secret header (used by the LiveKit agent)."""
    expected = os.environ.get("INTERNAL_API_SHARED_SECRET", "")
    provided = request.headers.get("X-Internal-API-Secret", "")
    if not expected or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )


def _has_valid_internal_secret(request: Request) -> bool:
    expected = os.environ.get("INTERNAL_API_SHARED_SECRET", "")
    provided = request.headers.get("X-Internal-API-Secret", "")
    return bool(expected and provided == expected)


@router.get("/branding", response_model=BrandingResponse)
async def get_branding(
    request: Request,
    current_user: User | None = Depends(get_optional_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> BrandingResponse:
    organization_id = current_user.organization_id if current_user else None
    if organization_id is None:
        if not _has_valid_internal_secret(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
            )
        org_header = request.headers.get("organization-id", "")
        try:
            organization_id = uuid.UUID(org_header)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="organization-id header is required",
            ) from exc

    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = org_result.scalar_one()

    cfg_result = await session.execute(
        select(HospitalConfig).where(HospitalConfig.organization_id == organization_id)
    )
    cfg = cfg_result.scalar_one_or_none()

    return BrandingResponse(
        hospital_name=org.name,
        branding=cfg.branding_json if cfg else {},
        agent_prompt_prefix=cfg.agent_prompt_prefix if cfg else None,
        vobiz_did=cfg.vobiz_did if cfg else None,
        whatsapp_channel_id=cfg.whatsapp_channel_id if cfg else None,
    )


@router.get("/doctors")
async def get_doctor_schedule(
    request: Request,
    organization_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_async_session),
) -> list:
    """
    Returns doctor schedules for a hospital. Protected by X-Internal-API-Secret.
    Used by the LiveKit agent to answer doctor schedule queries without hardcoded JSON.
    """
    _verify_internal_secret(request)

    doctors = (
        (
            await session.execute(
                select(User).where(
                    User.organization_id == organization_id,
                    User.role == "doctor",
                    User.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    return [
        {
            "doctor_name": d.name,
            "department": d.department or "General",
            "timings": d.timings or "By appointment",
            "fees": float(d.consultation_fee) if d.consultation_fee else 0,
            "availability": d.availability_json or [],
        }
        for d in doctors
    ]


@router.get("/hospitals")
async def list_hospitals(
    request: Request,
    current_user: User | None = Depends(get_optional_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Admin-only: list all hospital organizations."""
    if current_user is None:
        _verify_internal_secret(request)
    elif current_user.role not in ("superadmin", "hospital_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    orgs = (
        (
            await session.execute(
                select(Organization).where(
                    Organization.type == "hospital", Organization.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )

    return {
        "error": 0,
        "data": [{"id": str(o.id), "name": o.name, "slug": o.slug} for o in orgs],
    }
