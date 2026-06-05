from __future__ import annotations

import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.medivoice import MediVoiceSession, MediVoiceTranscription

router = APIRouter(prefix="/medivoice", tags=["medivoice"])


class SessionCreateIn(BaseModel):
    patient_name: str | None = None
    patient_mobile: str | None = None
    patient_email: str | None = None


class SessionUpdateIn(BaseModel):
    overall_summary: str | None = None
    diagnosis: str | None = None
    medicines: list | None = None
    revisit_date: date | None = None
    revisit_time: time | None = None
    meta_data: dict | None = None


class TranscriptIn(BaseModel):
    speaker: str  # doctor | patient
    text: str
    timestamp: float | None = None


@router.post("/sessions")
async def create_session(
    body: SessionCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    new = MediVoiceSession(
        id=uuid.uuid4(),
        organization_id=current_user.organization_id,
        doctor_id=current_user.id,
        patient_name=body.patient_name,
        patient_mobile=body.patient_mobile,
        patient_email=body.patient_email,
        medicines=[],
        meta_data={},
    )
    session.add(new)
    await session.flush()
    return {"error": 0, "session_id": str(new.id)}


@router.get("/sessions")
async def list_sessions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    rows = (await session.execute(
        select(MediVoiceSession)
        .where(
            MediVoiceSession.organization_id == current_user.organization_id,
            MediVoiceSession.doctor_id == current_user.id,
        )
        .order_by(MediVoiceSession.created_at.desc())
    )).scalars().all()

    return {
        "error": 0,
        "sessions": [
            {
                "id": str(s.id),
                "patient_name": s.patient_name,
                "patient_mobile": s.patient_mobile,
                "diagnosis": s.diagnosis,
                "revisit_date": str(s.revisit_date) if s.revisit_date else None,
                "created_at": s.created_at.isoformat(),
            }
            for s in rows
        ],
    }


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: uuid.UUID,
    body: SessionUpdateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    mv_session = await _get_session_or_404(session, session_id, current_user)

    if body.overall_summary is not None:
        mv_session.overall_summary = body.overall_summary
    if body.diagnosis is not None:
        mv_session.diagnosis = body.diagnosis
    if body.medicines is not None:
        mv_session.medicines = body.medicines
    if body.revisit_date is not None:
        mv_session.revisit_date = body.revisit_date
    if body.revisit_time is not None:
        mv_session.revisit_time = body.revisit_time
    if body.meta_data is not None:
        mv_session.meta_data = {**(mv_session.meta_data or {}), **body.meta_data}

    return {"error": 0, "msg": "Session updated"}


@router.post("/sessions/{session_id}/transcript")
async def add_transcript(
    session_id: uuid.UUID,
    body: TranscriptIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    await _get_session_or_404(session, session_id, current_user)

    session.add(MediVoiceTranscription(
        id=uuid.uuid4(),
        organization_id=current_user.organization_id,
        session_id=session_id,
        speaker=body.speaker,
        text=body.text,
        timestamp=body.timestamp,
    ))
    return {"error": 0, "msg": "Transcription added"}


@router.post("/sessions/{session_id}/notify")
async def notify_patient(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Trigger prescription WhatsApp notification and schedule revisit reminders."""
    await _get_session_or_404(session, session_id, current_user)

    from api.app.worker.tasks import schedule_reminder_calls, send_prescription_notifications
    send_prescription_notifications.delay(str(session_id))
    schedule_reminder_calls.delay(str(session_id))

    return {"error": 0, "msg": "Notifications queued"}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    mv = await _get_session_or_404(session, session_id, current_user)

    transcripts = (await session.execute(
        select(MediVoiceTranscription)
        .where(MediVoiceTranscription.session_id == session_id)
        .order_by(MediVoiceTranscription.timestamp)
    )).scalars().all()

    return {
        "error": 0,
        "session": {
            "id": str(mv.id),
            "patient_name": mv.patient_name,
            "patient_mobile": mv.patient_mobile,
            "patient_email": mv.patient_email,
            "overall_summary": mv.overall_summary,
            "diagnosis": mv.diagnosis,
            "medicines": mv.medicines,
            "revisit_date": str(mv.revisit_date) if mv.revisit_date else None,
            "revisit_time": str(mv.revisit_time) if mv.revisit_time else None,
            "meta_data": mv.meta_data,
            "created_at": mv.created_at.isoformat(),
        },
        "transcripts": [
            {"speaker": t.speaker, "text": t.text, "timestamp": t.timestamp}
            for t in transcripts
        ],
    }


async def _get_session_or_404(session, session_id: uuid.UUID, user: User) -> MediVoiceSession:
    mv = await session.scalar(
        select(MediVoiceSession).where(
            MediVoiceSession.id == session_id,
            MediVoiceSession.organization_id == user.organization_id,
        )
    )
    if mv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return mv
