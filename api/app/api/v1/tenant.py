from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.core.config import settings
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.organization import HospitalConfig, Organization
from api.app.schemas.tenant import BrandingResponse

router = APIRouter(prefix="/tenant", tags=["tenant"])


def _verify_internal_secret(request: Request) -> None:
    """Validate X-Internal-API-Secret header (used by the LiveKit agent)."""
    expected = os.environ.get("INTERNAL_API_SHARED_SECRET", "")
    provided = request.headers.get("X-Internal-API-Secret", "")
    if not expected or provided != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.get("/branding", response_model=BrandingResponse)
async def get_branding(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> BrandingResponse:
    org_result = await session.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    )
    org = org_result.scalar_one()

    cfg_result = await session.execute(
        select(HospitalConfig).where(
            HospitalConfig.organization_id == current_user.organization_id
        )
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

    doctors = (await session.execute(
        select(User).where(
            User.organization_id == organization_id,
            User.role == "doctor",
            User.is_active.is_(True),
        )
    )).scalars().all()

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
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Admin-only: list all hospital organizations."""
    if current_user.role not in ("superadmin", "hospital_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    orgs = (await session.execute(
        select(Organization).where(Organization.type == "hospital", Organization.is_active.is_(True))
    )).scalars().all()

    return {
        "error": 0,
        "data": [{"id": str(o.id), "name": o.name, "slug": o.slug} for o in orgs],
    }
