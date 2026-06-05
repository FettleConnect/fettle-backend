from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.call import (
    CallFeedback,
    CommunityEngagement,
    Escalation,
    InboundCall,
    InboundCommunityEngagement,
    InboundEscalation,
    InboundFeedback,
)
from api.app.models.hospital.patient import Patient
from api.app.models.organization import Organization
from api.app.schemas.hospital.feedback import (
    CallFeedbackIn,
    CommunityFeedbackIn,
    EscalationFeedbackIn,
    EscalationListResponse,
    EscalationOut,
    InboundCallFeedbackIn,
    SimpleOkResponse,
    UpdateCommunityIn,
    UpdateEscalationIn,
)

router = APIRouter(tags=["feedback"])


# ── Outbound feedback ─────────────────────────────────────────────────────────

@router.post("/calls/feedback", response_model=SimpleOkResponse)
async def post_call_feedback(
    body: CallFeedbackIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    org_id = current_user.organization_id
    patient = await _get_patient_in_org(session, body.patient_id, org_id)

    feedback = CallFeedback(
        id=uuid.uuid4(),
        organization_id=org_id,
        patient_id=patient.id,
        call_status=body.call_status,
        call_outcome=body.call_outcome,
        remarks=body.remarks,
        community_added=body.community_added,
        revisit_encouraged=body.revisit_encouraged,
        escalation_required=body.escalation_required,
        call_duration=body.call_duration,
        called_by=body.called_by,
        called_at=body.called_at,
    )
    session.add(feedback)
    return SimpleOkResponse(msg="Call feedback saved successfully")


@router.post("/escalations/feedback", response_model=SimpleOkResponse)
async def post_escalation_feedback(
    body: EscalationFeedbackIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    org_id = current_user.organization_id
    patient = await _get_patient_in_org(session, body.patient_id, org_id)

    # Get hospital name for WhatsApp notification
    org_name = await session.scalar(
        select(Organization.name).where(Organization.id == org_id)
    )

    session.add(Escalation(
        id=uuid.uuid4(),
        organization_id=org_id,
        patient_id=patient.id,
        issue_description=body.issue_description or "",
        department=body.department,
    ))

    # Notify admin via WhatsApp (async, fire-and-forget via background)
    _notify_escalation_bg(
        org_name=org_name or "Hospital",
        mobile=patient.mobile_no,
        issue=body.issue_description or "",
        call_type="Outbound Escalation",
    )

    return SimpleOkResponse(msg="Escalation feedback recorded")


@router.post("/escalations/update", response_model=SimpleOkResponse)
async def update_escalation(
    body: UpdateEscalationIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    esc = await session.scalar(
        select(Escalation).where(
            Escalation.id == body.id,
            Escalation.organization_id == current_user.organization_id,
        )
    )
    if esc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escalation not found")

    esc.status = body.status
    esc.resolution_notes = body.resolution_notes
    if body.status == "resolved":
        esc.resolved_at = datetime.now(tz=timezone.utc)
    return SimpleOkResponse(msg="Escalation updated.")


@router.get("/escalations/", response_model=EscalationListResponse)
async def list_escalations(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> EscalationListResponse:
    rows = (await session.execute(
        select(Escalation, Patient)
        .join(Patient, Escalation.patient_id == Patient.id)
        .where(Escalation.organization_id == current_user.organization_id)
        .order_by(Escalation.escalated_at.desc())
    )).all()

    org_name = await session.scalar(
        select(Organization.name).where(Organization.id == current_user.organization_id)
    ) or ""

    data = [
        EscalationOut(
            id=esc.id,
            issue_description=esc.issue_description,
            status=esc.status,
            department=esc.department,
            escalated_at=esc.escalated_at,
            resolved_at=esc.resolved_at,
            resolution_notes=esc.resolution_notes,
            patient_name=pat.patient_name,
            hospital_name=org_name,
        )
        for esc, pat in rows
    ]
    return EscalationListResponse(data=data)


@router.post("/calls/community", response_model=SimpleOkResponse)
async def post_community_feedback(
    body: CommunityFeedbackIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    org_id = current_user.organization_id
    patient = await _get_patient_in_org(session, body.patient_id, org_id)
    session.add(CommunityEngagement(
        id=uuid.uuid4(),
        organization_id=org_id,
        patient_id=patient.id,
        engagement_type=body.engagement_type,
        department=body.department,
    ))
    return SimpleOkResponse(msg="Community feedback recorded")


@router.post("/calls/community/update", response_model=SimpleOkResponse)
async def update_community(
    body: UpdateCommunityIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    comm = await session.scalar(
        select(CommunityEngagement).where(
            CommunityEngagement.id == body.id,
            CommunityEngagement.organization_id == current_user.organization_id,
        )
    )
    if comm is None:
        raise HTTPException(status_code=404, detail="Community record not found")
    comm.engagement_type = body.engagement_type
    comm.department = body.department
    return SimpleOkResponse(msg="Community updated")


# ── Inbound feedback ──────────────────────────────────────────────────────────

@router.post("/inbound/feedback", response_model=SimpleOkResponse)
async def post_inbound_feedback(
    body: InboundCallFeedbackIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    org_id = current_user.organization_id
    inbound_call = await session.scalar(
        select(InboundCall).where(
            InboundCall.id == body.patient_id,
            InboundCall.organization_id == org_id,
        )
    )
    if inbound_call is None:
        raise HTTPException(status_code=404, detail="Inbound call not found")

    session.add(InboundFeedback(
        id=uuid.uuid4(),
        organization_id=org_id,
        inbound_call_id=inbound_call.id,
        call_outcome=body.call_outcome,
        remarks=body.remarks,
        community_added=body.community_added,
        revisit_encouraged=body.revisit_encouraged,
        escalation_required=body.escalation_required,
        call_duration=body.call_duration,
        called_by=body.called_by,
    ))

    if body.community_added:
        session.add(InboundCommunityEngagement(
            id=uuid.uuid4(),
            organization_id=org_id,
            inbound_call_id=inbound_call.id,
            engagement_type="post",
            department=inbound_call.department,
        ))

    if body.escalation_required:
        session.add(InboundEscalation(
            id=uuid.uuid4(),
            organization_id=org_id,
            inbound_call_id=inbound_call.id,
            issue_description=body.remarks or "Inbound Escalation",
            department=inbound_call.department,
        ))
        admin_phone = os.getenv("ADMIN_PHONE", "+917032451866")
        _notify_escalation_bg(
            org_name="",
            mobile=inbound_call.from_phone_number or "",
            issue=body.remarks or "Inbound Escalation",
            call_type="Inbound Escalation",
            to_number=admin_phone,
        )

    return SimpleOkResponse(msg="Call feedback saved successfully")


@router.post("/inbound/escalation", response_model=SimpleOkResponse)
async def post_inbound_escalation(
    body: EscalationFeedbackIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    org_id = current_user.organization_id
    inbound_call = await session.scalar(
        select(InboundCall).where(
            InboundCall.id == body.patient_id,
            InboundCall.organization_id == org_id,
        )
    )
    if inbound_call is None:
        raise HTTPException(status_code=404, detail="Inbound call not found")

    session.add(InboundEscalation(
        id=uuid.uuid4(),
        organization_id=org_id,
        inbound_call_id=inbound_call.id,
        issue_description=body.issue_description or "",
        department=body.department,
    ))
    return SimpleOkResponse(msg="Escalation feedback recorded")


@router.post("/inbound/escalation/update", response_model=SimpleOkResponse)
async def update_inbound_escalation(
    body: UpdateEscalationIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    esc = await session.scalar(
        select(InboundEscalation).where(
            InboundEscalation.id == body.id,
            InboundEscalation.organization_id == current_user.organization_id,
        )
    )
    if esc is None:
        raise HTTPException(status_code=404, detail="Inbound escalation not found")
    esc.status = body.status
    esc.resolution_notes = body.resolution_notes
    if body.status == "resolved":
        esc.resolved_at = datetime.now(tz=timezone.utc)
    return SimpleOkResponse(msg="Escalation updated.")


@router.post("/inbound/community", response_model=SimpleOkResponse)
async def post_inbound_community(
    body: CommunityFeedbackIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> SimpleOkResponse:
    org_id = current_user.organization_id
    inbound_call = await session.scalar(
        select(InboundCall).where(
            InboundCall.id == body.patient_id,
            InboundCall.organization_id == org_id,
        )
    )
    if inbound_call is None:
        raise HTTPException(status_code=404, detail="Inbound call not found")
    session.add(InboundCommunityEngagement(
        id=uuid.uuid4(),
        organization_id=org_id,
        inbound_call_id=inbound_call.id,
        engagement_type=body.engagement_type,
        department=body.department,
    ))
    return SimpleOkResponse(msg="Community feedback recorded")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_patient_in_org(session, patient_id: uuid.UUID, org_id: uuid.UUID) -> Patient:
    patient = await session.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.organization_id == org_id,
        )
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found in your organization",
        )
    return patient


def _notify_escalation_bg(
    org_name: str,
    mobile: str,
    issue: str,
    call_type: str,
    to_number: str | None = None,
) -> None:
    """Fire-and-forget: notify admin of escalation via WhatsApp.
    Runs synchronously in a background thread via asyncio.ensure_future if loop is running.
    """
    import asyncio

    from api.app.services.whatsapp import send_whatsapp

    admin_phone = to_number or os.getenv("ADMIN_PHONE", "+917032451866")

    async def _send() -> None:
        await send_whatsapp(
            to_number=admin_phone,
            template_name="escalation_info",
            template_params=[org_name, mobile, issue, call_type],
        )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_send())
    except RuntimeError:
        pass
