from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import MediVoiceSession


def _parse_revisit_time(value: str | None) -> time | None:
    if not value:
        return None

    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, pattern).time()
        except ValueError:
            continue
    return None


def _session_revisit_datetime(session: MediVoiceSession) -> datetime | None:
    if session.revisit_date is None:
        return None

    revisit_time = _parse_revisit_time(session.revisit_time)
    if revisit_time is None:
        return None

    return datetime.combine(session.revisit_date, revisit_time)


@celery_app.task(name="send_prescription_notifications")
def send_prescription_notifications(session_id: str) -> dict[str, Any]:
    session = SessionLocal()
    try:
        medivoice_session = session.get(MediVoiceSession, session_id)
        if medivoice_session is None:
            return {"session_id": session_id, "status": "session_not_found"}

        result = reminder_task(session_id, "prescription")
        return {
            "session_id": session_id,
            "patient_name": medivoice_session.patient_name,
            "patient_mobile": medivoice_session.patient_mobile,
            "notification": result,
            "status": "sent" if result["status"] == "queued" else result["status"],
        }
    finally:
        session.close()


@celery_app.task(name="reminder_task")
def reminder_task(session_id: str, reminder_type: str) -> dict[str, Any]:
    session = SessionLocal()
    try:
        medivoice_session = session.get(MediVoiceSession, session_id)
        if medivoice_session is None:
            return {
                "session_id": session_id,
                "reminder_type": reminder_type,
                "status": "session_not_found",
            }
        meta_data = dict(medivoice_session.meta_data or {})
        reminders = list(meta_data.get("reminders", []))
        reminders.append(
            {
                "type": reminder_type,
                "queued_at": datetime.utcnow().isoformat(),
                "mobile": medivoice_session.patient_mobile,
            }
        )
        meta_data["reminders"] = reminders
        medivoice_session.meta_data = meta_data
        session.commit()
        return {
            "session_id": session_id,
            "reminder_type": reminder_type,
            "status": "queued",
        }
    finally:
        session.close()


@celery_app.task(name="schedule_reminder_calls")
def schedule_reminder_calls(session_id: str) -> dict[str, Any]:
    session = SessionLocal()
    try:
        medivoice_session = session.get(MediVoiceSession, session_id)
        if medivoice_session is None:
            return {"session_id": session_id, "status": "session_not_found"}

        revisit_dt = _session_revisit_datetime(medivoice_session)
        if revisit_dt is None:
            return {"session_id": session_id, "status": "missing_revisit_datetime"}

        scheduled = []
        for reminder_type, offset in (
            ("24h", timedelta(hours=24)),
            ("1h", timedelta(hours=1)),
        ):
            eta = revisit_dt - offset
            reminder_task.apply_async(args=[session_id, reminder_type], eta=eta)
            scheduled.append({"reminder_type": reminder_type, "eta": eta.isoformat()})

        meta_data = dict(medivoice_session.meta_data or {})
        meta_data["scheduled_reminders"] = scheduled
        medivoice_session.meta_data = meta_data
        session.commit()
        return {"session_id": session_id, "scheduled": scheduled, "status": "scheduled"}
    finally:
        session.close()
