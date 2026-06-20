from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.call import InboundCall, OutboundCall
from api.app.models.hospital.campaign import Campaign
from api.app.models.hospital.patient import Patient
from api.app.models.organization import HospitalConfig, Organization

router = APIRouter(tags=["calls"])


# ── Outbound dispatch ─────────────────────────────────────────────────────────


@router.post("/calls/outbound")
async def trigger_outbound(
    body: dict,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Trigger outbound call(s).
    - is_individual=True + patient_ids → individual calls, queued immediately
    - is_individual=False → create a Campaign and dispatch run_campaign_task
    """
    from api.app.worker.tasks import call_outbound_task, run_campaign_task

    org_id = current_user.organization_id
    is_individual = body.get("is_individual", False)
    patient_ids: list[str] = body.get("patient_ids", [])
    start_date_str: str | None = body.get("start_date")
    end_date_str: str | None = body.get("end_date")
    unconnected_only: bool = body.get("unconnected_only", False)
    audience_type: str = body.get("audience_type", "all")

    # Fetch org name for metadata
    org_name = (
        await session.scalar(select(Organization.name).where(Organization.id == org_id))
        or ""
    )

    if is_individual and patient_ids:
        # Individual calls — queue immediately
        patient_rows = (
            (
                await session.execute(
                    select(Patient).where(
                        Patient.id.in_([uuid.UUID(pid) for pid in patient_ids]),
                        Patient.organization_id == org_id,
                    )
                )
            )
            .scalars()
            .all()
        )

        task_ids = []
        for p in patient_rows:
            task = call_outbound_task.delay(
                {
                    "organization_id": str(org_id),
                    "patient_id": str(p.id),
                    "campaign_id": None,
                    "prompt": body.get("purpose", ""),
                }
            )
            task_ids.append(task.id)

        return {
            "error": 0,
            "msg": "Outbound call(s) queued",
            "patients": [
                {"id": str(p.id), "name": p.patient_name} for p in patient_rows
            ],
            "task_ids": task_ids,
        }

    # Campaign mode
    campaign_id_str = body.get("campaign_id")
    campaign_obj: Campaign | None = None

    if campaign_id_str:
        campaign_obj = await session.scalar(
            select(Campaign).where(
                Campaign.id == uuid.UUID(campaign_id_str),
                Campaign.organization_id == org_id,
            )
        )
        if not campaign_obj:
            raise HTTPException(status_code=404, detail="Campaign not found")
    else:
        start_date = date.fromisoformat(start_date_str) if start_date_str else None
        end_date = date.fromisoformat(end_date_str) if end_date_str else None

        campaign_obj = Campaign(
            id=uuid.uuid4(),
            organization_id=org_id,
            name=body.get("name") or body.get("campaign_name") or "Default Campaign",
            purpose=body.get("purpose", ""),
            audience_type=audience_type,
            patient_ids=patient_ids,
            start_date=start_date,
            end_date=end_date,
            unconnected_only=unconnected_only,
            status="created",
        )
        session.add(campaign_obj)
        await session.flush()

    run_campaign_task.delay(str(campaign_obj.id))

    return {
        "error": 0,
        "msg": f"Campaign '{campaign_obj.name}' initiated. Calls will respect concurrency limits.",
        "campaign_id": str(campaign_obj.id),
    }


@router.post("/calls/outbound/process")
async def process_outbound(
    body: dict,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Trigger Celery task to pull S3 transcript and process a completed outbound call."""
    from api.app.worker.tasks import process_outbound_calls

    process_outbound_calls.delay(body)
    return {"error": 0, "msg": "Processing queued"}


# ── Inbound list ──────────────────────────────────────────────────────────────


@router.get("/inbound/")
async def list_inbound_calls(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    org_id = current_user.organization_id

    q = (
        select(InboundCall)
        .where(InboundCall.organization_id == org_id)
        .order_by(InboundCall.started_at.desc())
    )
    if start_date and end_date:
        q = q.where(func.date(InboundCall.started_at).between(start_date, end_date))

    rows = (await session.execute(q)).scalars().all()

    def _normalise(cp: str) -> str:
        return cp if cp != "not_happened" else "not_connected"

    return {
        "error": 0,
        "data": [
            {
                "id": str(r.id),
                "from_phone_number": r.from_phone_number,
                "to_phone_number": r.to_phone_number,
                "status": _normalise(r.calling_process),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "department": r.department,
            }
            for r in rows
        ],
        "count": len(rows),
    }


# ── Excel export ──────────────────────────────────────────────────────────────


@router.get("/calls/export")
async def export_outbound_excel(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    org_id = current_user.organization_id

    rows = (
        await session.execute(
            select(
                Patient.patient_name,
                Patient.mobile_no,
                OutboundCall.calling_process,
                OutboundCall.ended_reason,
                OutboundCall.started_at,
            )
            .join(Patient, OutboundCall.patient_id == Patient.id)
            .where(OutboundCall.organization_id == org_id)
            .order_by(OutboundCall.started_at.desc())
        )
    ).all()

    data = [
        {
            "Patient Name": r.patient_name,
            "Mobile No": r.mobile_no,
            "Status": r.calling_process,
            "Reason": r.ended_reason or "",
            "Call Date": (
                r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "N/A"
            ),
        }
        for r in rows
    ]

    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)

    filename = f"outbound_report_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
