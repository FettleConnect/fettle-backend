from __future__ import annotations

import uuid
from datetime import date, time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass

from api.app.api.deps import get_current_user
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.medivoice import MediVoiceSession, MediVoiceTranscription
from api.app.models.organization import Organization

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
    rows = (
        (
            await session.execute(
                select(MediVoiceSession)
                .where(
                    MediVoiceSession.organization_id == current_user.organization_id,
                    MediVoiceSession.doctor_id == current_user.id,
                )
                .order_by(MediVoiceSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

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

    session.add(
        MediVoiceTranscription(
            id=uuid.uuid4(),
            organization_id=current_user.organization_id,
            session_id=session_id,
            speaker=body.speaker,
            text=body.text,
            timestamp=body.timestamp,
        )
    )
    return {"error": 0, "msg": "Transcription added"}


@router.post("/sessions/{session_id}/notify")
async def notify_patient(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Trigger prescription WhatsApp notification and schedule revisit reminders."""
    await _get_session_or_404(session, session_id, current_user)

    from api.app.worker.tasks import (
        schedule_reminder_calls,
        send_prescription_notifications,
    )

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

    transcripts = (
        (
            await session.execute(
                select(MediVoiceTranscription)
                .where(MediVoiceTranscription.session_id == session_id)
                .order_by(MediVoiceTranscription.timestamp)
            )
        )
        .scalars()
        .all()
    )

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


class SyncIn(BaseModel):
    doctorEmail: str
    patientName: str | None = None
    patientMobile: str | None = None
    patientEmail: str | None = None
    overallSummary: str | None = None
    diagnosis: str | None = None
    medicines: list | None = None
    revisitDate: date | None = None
    revisitTime: time | None = None
    metaData: dict | None = None
    transcriptions: list[dict] | None = None


@router.post("/sync")
async def sync_session(
    request: Request,
    body: SyncIn,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    External sync webhook — creates a MediVoice session from an external source
    (e.g. the MediVoice PWA). Protected by X-Fettle-Secret header.
    """
    import os

    secret_env = os.environ.get("INTERNAL_API_SHARED_SECRET", "")
    provided = request.headers.get("X-Fettle-Secret", "")
    if not secret_env or provided != secret_env:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    # Lookup doctor by email
    doctor = await session.scalar(
        select(User).where(
            User.email == body.doctorEmail.lower().strip(), User.role == "doctor"
        )
    )
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found")

    meta = body.metaData or {}
    diagnosis = body.diagnosis or meta.get("diagnosis") or meta.get("clinicalSummary")
    medicines = body.medicines or meta.get("medicines") or meta.get("medications") or []
    revisit_date = (
        body.revisitDate or meta.get("revisit_date") or meta.get("revisitDate")
    )
    revisit_time = body.revisitTime or meta.get("revisit_time")

    mv = MediVoiceSession(
        id=uuid.uuid4(),
        organization_id=doctor.organization_id,
        doctor_id=doctor.id,
        patient_name=body.patientName,
        patient_mobile=body.patientMobile,
        patient_email=body.patientEmail,
        overall_summary=body.overallSummary,
        diagnosis=diagnosis,
        medicines=medicines,
        revisit_date=revisit_date,
        revisit_time=revisit_time,
        meta_data=meta,
    )
    session.add(mv)
    await session.flush()

    for t in body.transcriptions or []:
        session.add(
            MediVoiceTranscription(
                id=uuid.uuid4(),
                organization_id=doctor.organization_id,
                session_id=mv.id,
                speaker=t.get("speaker", ""),
                text=t.get("text", ""),
                timestamp=t.get("timestamp", 0.0),
            )
        )

    from api.app.worker.tasks import (
        schedule_reminder_calls,
        send_prescription_notifications,
    )

    send_prescription_notifications.delay(str(mv.id))
    schedule_reminder_calls.delay(str(mv.id))

    return {"error": 0, "msg": "Sync successful", "session_id": str(mv.id)}


@router.get("/sessions/{session_id}/artifacts/{artifact_type}")
async def get_session_artifact(
    session_id: uuid.UUID,
    artifact_type: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> "Response":
    """Stream session artifact (transcript, audio) from S3."""
    from fastapi.responses import Response as _Response

    from api.app.services.s3 import get_presigned_url

    if artifact_type not in ("transcript", "audio", "pdf"):
        raise HTTPException(
            status_code=400, detail="Invalid artifact type. Use: transcript, audio, pdf"
        )

    mv = await _get_session_or_404(session, session_id, current_user)

    if artifact_type == "audio":
        # Redirect to presigned URL
        url = get_presigned_url(f"s3://fettle-sessions/{session_id}/audio.ogg")
        return _Response(status_code=302, headers={"Location": url or ""})

    if artifact_type == "transcript":
        # Build transcript text from DB
        transcripts = (
            (
                await session.execute(
                    select(MediVoiceTranscription)
                    .where(MediVoiceTranscription.session_id == session_id)
                    .order_by(MediVoiceTranscription.timestamp)
                )
            )
            .scalars()
            .all()
        )

        lines = [f"[{t.speaker.upper()}] {t.text}" for t in transcripts]
        content = "\n".join(lines).encode()
        return _Response(
            content=content,
            media_type="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="transcript_{session_id}.txt"'
            },
        )

    # PDF — basic HTML-to-text fallback (WeasyPrint not guaranteed in env)
    org = await session.scalar(
        select(Organization).where(Organization.id == current_user.organization_id)
    )
    doctor = await session.scalar(select(User).where(User.id == mv.doctor_id))
    transcripts = (
        (
            await session.execute(
                select(MediVoiceTranscription)
                .where(MediVoiceTranscription.session_id == session_id)
                .order_by(MediVoiceTranscription.timestamp)
            )
        )
        .scalars()
        .all()
    )

    lines = [
        f"Hospital: {org.name if org else 'N/A'}",
        f"Doctor: {doctor.name if doctor else 'N/A'}",
        f"Patient: {mv.patient_name or 'N/A'}",
        f"Date: {mv.created_at.strftime('%Y-%m-%d')}",
        "",
        f"Diagnosis: {mv.diagnosis or 'N/A'}",
        f"Summary: {mv.overall_summary or 'N/A'}",
        "",
        "Prescription:",
        *(
            (
                f"  - {m}"
                if isinstance(m, str)
                else f"  - {m.get('name')} {m.get('dosage','')}"
            )
            for m in (mv.medicines or [])
        ),
        "",
        "Transcript:",
        *(f"  [{t.speaker.upper()}] {t.text}" for t in transcripts),
    ]
    content = "\n".join(lines).encode()
    return _Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="prescription_{session_id}.txt"'
        },
    )


async def _get_session_or_404(
    session, session_id: uuid.UUID, user: User
) -> MediVoiceSession:
    mv = await session.scalar(
        select(MediVoiceSession).where(
            MediVoiceSession.id == session_id,
            MediVoiceSession.organization_id == user.organization_id,
        )
    )
    if mv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return mv
