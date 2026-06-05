from __future__ import annotations

"""
Hospital management endpoints:
- Campaign CRUD (list, delete)
- Doctor management (list, create, staff availability)
- WhatsApp broadcast text (set/get)
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.core.security import hash_password
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.call import OutboundCall
from api.app.models.hospital.campaign import Campaign
from api.app.models.organization import Organization

router = APIRouter(tags=["management"])


# ── Campaign CRUD ─────────────────────────────────────────────────────────────

@router.get("/campaigns/")
async def list_campaigns(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    org_id = current_user.organization_id
    campaigns = (await session.execute(
        select(Campaign)
        .where(Campaign.organization_id == org_id)
        .order_by(Campaign.created_at.desc())
    )).scalars().all()

    data = []
    for c in campaigns:
        total_calls = (await session.scalar(
            select(func.count(OutboundCall.id))
            .where(OutboundCall.campaign_id == c.id)
        )) or 0
        connected_calls = (await session.scalar(
            select(func.count(OutboundCall.id))
            .where(OutboundCall.campaign_id == c.id, OutboundCall.calling_process == "connected")
        )) or 0
        data.append({
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "audience_type": c.audience_type,
            "purpose": c.purpose,
            "start_date": str(c.start_date) if c.start_date else None,
            "end_date": str(c.end_date) if c.end_date else None,
            "created_at": c.created_at.isoformat(),
            "stats": {"total_calls": total_calls, "connected_calls": connected_calls},
        })

    return {"error": 0, "data": data}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    camp = await session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.organization_id == current_user.organization_id,
        )
    )
    if camp is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await session.delete(camp)
    return {"error": 0, "msg": "Campaign deleted"}


# ── Doctor management ─────────────────────────────────────────────────────────

class DoctorCreateIn(BaseModel):
    name: str
    email: str
    password: str
    department: str | None = None
    mobile_number: str | None = None
    consultation_fee: float | None = None
    timings: str | None = None
    availability_json: list | None = None


@router.get("/doctors/")
async def list_doctors(
    doctor_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    q = select(User).where(
        User.organization_id == current_user.organization_id,
        User.role == "doctor",
        User.is_active.is_(True),
    )
    if doctor_id:
        try:
            q = q.where(User.id == uuid.UUID(doctor_id))
        except ValueError:
            pass

    doctors = (await session.execute(q)).scalars().all()
    return {
        "error": 0,
        "data": [
            {
                "id": str(d.id),
                "name": d.name,
                "email": d.email,
                "mobile_no": d.mobile_number,
                "department": d.department,
                "consultation_fee": float(d.consultation_fee) if d.consultation_fee else None,
                "timings": d.timings,
                "availability": d.availability_json,
                "created_at": d.created_at.isoformat(),
            }
            for d in doctors
        ],
    }


@router.post("/doctors/")
async def create_doctor(
    body: DoctorCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    # Check duplicate email within org
    existing = await session.scalar(
        select(User).where(
            User.organization_id == current_user.organization_id,
            User.email == body.email.lower().strip(),
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists in this organization",
        )

    doctor = User(
        id=uuid.uuid4(),
        organization_id=current_user.organization_id,
        email=body.email.lower().strip(),
        password_hash=hash_password(body.password),
        name=body.name,
        role="doctor",
        department=body.department,
        mobile_number=body.mobile_number,
        consultation_fee=body.consultation_fee,
        timings=body.timings,
        availability_json=body.availability_json or [],
    )
    session.add(doctor)
    await session.flush()
    return {"error": 0, "msg": "Doctor account created", "id": str(doctor.id)}


@router.get("/doctors/availability")
async def staff_availability(
    doctor_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    q = select(User).where(
        User.organization_id == current_user.organization_id,
        User.role == "doctor",
        User.is_active.is_(True),
    )
    if doctor_id and doctor_id.lower() != "all":
        try:
            q = q.where(User.id == uuid.UUID(doctor_id))
        except ValueError:
            pass

    doctors = (await session.execute(q)).scalars().all()
    return {
        "error": 0,
        "data": [
            {
                "id": str(d.id),
                "name": d.name,
                "department": d.department,
                "availability": d.availability_json,
            }
            for d in doctors
        ],
    }


# ── WhatsApp broadcast text ────────────────────────────────────────────────────

class BroadcastTextIn(BaseModel):
    text: str | None = None
    template_name: str | None = None
    target_numbers: list[str] | None = None  # if provided, broadcast immediately


@router.post("/calls/broadcast")
async def set_broadcast_text(
    body: BroadcastTextIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Set default WhatsApp broadcast text for the hospital.
    If target_numbers is provided, immediately sends the message via Vobiz.
    """
    from api.app.models.organization import HospitalConfig

    cfg = await session.scalar(
        select(HospitalConfig).where(
            HospitalConfig.organization_id == current_user.organization_id
        )
    )

    if cfg is None:
        # Create minimal config
        cfg = HospitalConfig(
            id=uuid.uuid4(),
            organization_id=current_user.organization_id,
            branding_json={},
        )
        session.add(cfg)

    # Store the broadcast text in branding_json for now
    branding = cfg.branding_json or {}
    if body.text:
        branding["broadcast_text"] = body.text
        cfg.branding_json = branding

    await session.flush()

    # If target numbers provided, send immediately (fire-and-forget)
    if body.target_numbers:
        org = await session.scalar(
            select(Organization).where(Organization.id == current_user.organization_id)
        )
        import asyncio
        from api.app.services.whatsapp import send_whatsapp

        async def _broadcast():
            for number in body.target_numbers:
                n = number.strip()
                if n.endswith(".0"):
                    n = n[:-2]
                if not n.startswith("+"):
                    n = "+91" + n

                if body.template_name:
                    await send_whatsapp(
                        to_number=n,
                        template_name=body.template_name,
                        template_params=[org.name if org else "", body.text or ""],
                        channel_id=cfg.whatsapp_channel_id,
                    )
                else:
                    await send_whatsapp(
                        to_number=n,
                        msg=body.text or "",
                        channel_id=cfg.whatsapp_channel_id,
                    )

        asyncio.create_task(_broadcast())
        return {"error": 0, "msg": "Broadcast initiated", "count": len(body.target_numbers)}

    return {"error": 0, "msg": "Template saved"}


@router.get("/calls/broadcast")
async def get_broadcast_text(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.models.organization import HospitalConfig

    cfg = await session.scalar(
        select(HospitalConfig).where(
            HospitalConfig.organization_id == current_user.organization_id
        )
    )
    text = (cfg.branding_json or {}).get("broadcast_text", "") if cfg else ""
    return {"error": 0, "data": text}
