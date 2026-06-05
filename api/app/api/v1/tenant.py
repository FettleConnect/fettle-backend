from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.organization import HospitalConfig, Organization
from api.app.schemas.tenant import BrandingResponse

router = APIRouter(prefix="/tenant", tags=["tenant"])


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
