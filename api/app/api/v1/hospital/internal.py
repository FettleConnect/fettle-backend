from __future__ import annotations

"""
Internal API endpoints — protected by X-Internal-API-Secret header.
Called by the LiveKit agent process, NOT by browser clients.
"""

import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db.session import get_async_session
from api.app.models.hospital.call import (CallFeedback, Escalation,
                                          InboundCall, OutboundCall)
from api.app.models.hospital.patient import Patient
from api.app.models.organization import Organization

router = APIRouter(tags=["internal"])


def _check_secret(request: Request) -> None:
    expected = os.environ.get("INTERNAL_API_SHARED_SECRET", "")
    provided = request.headers.get("X-Internal-API-Secret", "")
    if not expected or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )


def _normalize_phone(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


@router.get("/patient_lookup_by_phone/")
async def patient_lookup_by_phone(
    request: Request,
    phone_number: str = Query(...),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Lookup patient records by phone number across all organizations.
    Returns full call history, previous appointments, escalations.
    Used by the LiveKit agent to personalize inbound greetings.
    """
    _check_secret(request)

    phone_number = phone_number.strip()
    if not phone_number:
        raise HTTPException(status_code=400, detail="phone_number is required")

    normalized = _normalize_phone(phone_number)
    if not normalized:
        return {"error": 1, "errorMsg": "Invalid phone_number"}

    suffix = normalized[-10:] if len(normalized) > 10 else normalized

    # Find patients matching this phone suffix
    patients = (
        (
            await session.execute(
                select(Patient).where(
                    or_(
                        Patient.mobile_no.like(f"%{suffix}"),
                        Patient.mobile_no.like(f"%{normalized}"),
                        Patient.mobile_no == phone_number,
                    )
                )
            )
        )
        .scalars()
        .all()
    )

    if not patients:
        return {"error": 1, "errorMsg": "Patient not found"}

    org_ids = list({p.organization_id for p in patients})

    # Org name lookup
    org_names = {
        o.id: o.name
        for o in (
            await session.execute(
                select(Organization).where(Organization.id.in_(org_ids))
            )
        )
        .scalars()
        .all()
    }

    patient_ids = [p.id for p in patients]

    # Previous appointments (patients with visit_date)
    prev_appts = (
        (
            await session.execute(
                select(Patient)
                .where(
                    or_(
                        Patient.mobile_no.like(f"%{suffix}"),
                        Patient.mobile_no.like(f"%{normalized}"),
                    ),
                    Patient.visit_date.isnot(None),
                )
                .order_by(Patient.visit_date.desc())
            )
        )
        .scalars()
        .all()
    )

    # Outbound calls
    outbound_calls = (
        (
            await session.execute(
                select(OutboundCall)
                .where(OutboundCall.patient_id.in_(patient_ids))
                .order_by(OutboundCall.started_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # Inbound calls matching phone
    inbound_calls = (
        (
            await session.execute(
                select(InboundCall)
                .where(
                    or_(
                        InboundCall.from_phone_number.like(f"%{suffix}"),
                        InboundCall.from_phone_number == phone_number,
                    )
                )
                .order_by(InboundCall.started_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # Feedback
    feedback = (
        (
            await session.execute(
                select(CallFeedback)
                .where(CallFeedback.patient_id.in_(patient_ids))
                .order_by(CallFeedback.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # Escalations
    escalations = (
        (
            await session.execute(
                select(Escalation)
                .where(Escalation.patient_id.in_(patient_ids))
                .order_by(Escalation.escalated_at.desc())
            )
        )
        .scalars()
        .all()
    )

    patient_data = [
        {
            "id": str(p.id),
            "patient_name": p.patient_name,
            "mobile_no": p.mobile_no,
            "age": p.age,
            "department": p.department,
            "hospital_name": org_names.get(p.organization_id, ""),
            "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
            "previous_appointments": [
                {
                    "id": str(a.id),
                    "patient_name": a.patient_name,
                    "mobile_no": a.mobile_no,
                    "department": a.department,
                    "date": str(a.visit_date) if a.visit_date else None,
                    "uploaded_at": a.uploaded_at.isoformat() if a.uploaded_at else None,
                    "hospital_name": org_names.get(a.organization_id, ""),
                }
                for a in prev_appts
                if a.id != p.id
            ],
            "call_history": {
                "outbound_calls": [
                    {
                        "id": str(c.id),
                        "status": c.calling_process,
                        "calling_process": c.calling_process,
                        "started_at": (
                            c.started_at.isoformat() if c.started_at else None
                        ),
                        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
                        "ended_reason": c.ended_reason,
                        "patient_name": p.patient_name,
                        "mobile_no": p.mobile_no,
                        "department": p.department,
                        "hospital_name": org_names.get(p.organization_id, ""),
                    }
                    for c in outbound_calls
                    if c.patient_id == p.id
                ],
                "inbound_calls": [
                    {
                        "id": str(c.id),
                        "status": c.calling_process,
                        "calling_process": c.calling_process,
                        "started_at": (
                            c.started_at.isoformat() if c.started_at else None
                        ),
                        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
                        "from_phone_number": c.from_phone_number,
                        "department": c.department,
                    }
                    for c in inbound_calls
                ],
                "feedback": [
                    {
                        "id": str(f.id),
                        "call_status": f.call_status,
                        "call_outcome": f.call_outcome,
                        "remarks": f.remarks,
                        "community_added": f.community_added,
                        "revisit_encouraged": f.revisit_encouraged,
                        "escalation_required": f.escalation_required,
                        "call_duration": f.call_duration,
                        "called_by": f.called_by,
                        "called_at": f.called_at.isoformat() if f.called_at else None,
                        "created_at": f.created_at.isoformat(),
                        "patient_name": p.patient_name,
                        "mobile_no": p.mobile_no,
                        "department": p.department,
                        "hospital_name": org_names.get(p.organization_id, ""),
                    }
                    for f in feedback
                    if f.patient_id == p.id
                ],
                "escalations": [
                    {
                        "id": str(e.id),
                        "issue_description": e.issue_description,
                        "status": e.status,
                        "department": e.department,
                        "escalated_at": e.escalated_at.isoformat(),
                        "resolved_at": (
                            e.resolved_at.isoformat() if e.resolved_at else None
                        ),
                        "resolution_notes": e.resolution_notes,
                        "patient_name": p.patient_name,
                        "mobile_no": p.mobile_no,
                        "hospital_name": org_names.get(p.organization_id, ""),
                    }
                    for e in escalations
                    if e.patient_id == p.id
                ],
            },
        }
        for p in patients
    ]

    return {"error": 0, "count": len(patient_data), "data": patient_data}


@router.post("/internal/escalation/")
@router.post("/internal/escalation")
async def agent_escalation(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Agent-initiated escalation endpoint — protected by X-Internal-API-Secret.
    Called by the LiveKit agent when a patient needs urgent assistance.
    Lookup patient by phone_number OR patient_id. Creates Escalation record +
    sends WhatsApp notification to the hospital admin.
    """
    _check_secret(request)

    try:
        data = await request.json()
    except Exception:
        data = {}

    patient_id_str = data.get("patient_id")
    phone_number = data.get("phone_number", "").strip()
    issue_description = data.get("issue_description", "Agent-detected escalation")
    department = data.get("department", "")

    patient: Patient | None = None

    # Try lookup by patient_id first
    if patient_id_str and patient_id_str not in (None, "", "0", 0):
        try:
            patient = await session.scalar(
                select(Patient).where(Patient.id == uuid.UUID(patient_id_str))
            )
        except (ValueError, AttributeError):
            pass

    # Fall back to phone number lookup
    if patient is None and phone_number:
        normalized = _normalize_phone(phone_number)
        suffix = normalized[-10:] if len(normalized) > 10 else normalized
        patient = await session.scalar(
            select(Patient).where(
                or_(
                    Patient.mobile_no.like(f"%{suffix}"),
                    Patient.mobile_no == phone_number,
                )
            )
        )

    if patient is None:
        return {"error": 1, "msg": "Patient not found"}

    # Create escalation
    esc = Escalation(
        id=uuid.uuid4(),
        organization_id=patient.organization_id,
        patient_id=patient.id,
        issue_description=issue_description,
        department=department or patient.department,
    )
    session.add(esc)
    await session.flush()

    # Notify admin via WhatsApp (fire-and-forget)
    import asyncio

    from api.app.models.organization import Organization as Org

    org_name = (
        await session.scalar(select(Org.name).where(Org.id == patient.organization_id))
        or "Hospital"
    )

    async def _notify() -> None:
        from api.app.services.whatsapp import send_whatsapp

        admin_phone = os.environ.get("ADMIN_PHONE", "+917032451866")
        await send_whatsapp(
            to_number=admin_phone,
            template_name="escalation_info",
            template_params=[
                org_name,
                patient.mobile_no,
                issue_description,
                "Automated Escalation",
            ],
        )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_notify())
    except RuntimeError:
        pass

    return {"error": 0, "msg": "Escalation recorded"}
