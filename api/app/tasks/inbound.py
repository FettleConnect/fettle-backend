from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import InboundHospital


@celery_app.task(name="inbound_call_task")
def inbound_call_task(json_payload: dict[str, Any]) -> dict[str, Any]:
    session = SessionLocal()
    try:
        call = InboundHospital(
            hospital_id=_uuid_or_none(json_payload.get("hospital_id")),
            vapi_id=_first(json_payload, "vapi_id", "call_id", "id"),
            status=json_payload.get("status"),
            endedReason=_first(json_payload, "endedReason", "ended_reason"),
            ended_at=_datetime_or_none(_first(json_payload, "ended_at", "endedAt")),
            message_s3_link=_first(
                json_payload, "message_s3_link", "transcript_url", "transcript"
            ),
            audio_link=_first(json_payload, "audio_link", "recording_url", "audio_url"),
            task_id=json_payload.get("task_id"),
            calling_process=json_payload.get("calling_process", "received"),
            from_phone_number=_first(
                json_payload, "from_phone_number", "from", "caller"
            ),
            to_phone_numnber=_first(
                json_payload, "to_phone_numnber", "to_phone_number", "to"
            ),
            department=json_payload.get("department", "N/A"),
        )
        session.add(call)
        session.commit()
        return {"task": "inbound_call_task", "id": str(call.id), "status": "persisted"}
    finally:
        session.close()


@celery_app.task(name="process_inbound_calls")
def process_inbound_calls(json_payload: dict[str, Any]) -> dict[str, Any]:
    session = SessionLocal()
    try:
        call = _find_inbound_call(session, json_payload)
        if call is None:
            return {"task": "process_inbound_calls", "status": "call_not_found"}

        call.status = json_payload.get("status", call.status)
        call.endedReason = (
            _first(json_payload, "endedReason", "ended_reason") or call.endedReason
        )
        call.ended_at = (
            _datetime_or_none(_first(json_payload, "ended_at", "endedAt"))
            or call.ended_at
        )
        call.message_s3_link = (
            _first(json_payload, "message_s3_link", "transcript_url", "transcript")
            or call.message_s3_link
        )
        call.audio_link = (
            _first(json_payload, "audio_link", "recording_url", "audio_url")
            or call.audio_link
        )
        call.task_id_process = json_payload.get("task_id_process", call.task_id_process)
        call.calling_process = json_payload.get("calling_process", "processed")
        session.commit()
        return {
            "task": "process_inbound_calls",
            "id": str(call.id),
            "status": "processed",
        }
    finally:
        session.close()


def _first(payload: dict[str, Any], *keys: str) -> Any:
    return next((payload[key] for key in keys if payload.get(key) is not None), None)


def _uuid_or_none(value: Any) -> UUID | None:
    if not value:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _find_inbound_call(session, payload: dict[str, Any]) -> InboundHospital | None:
    call_id = _uuid_or_none(payload.get("id") or payload.get("inbound_id"))
    if call_id:
        return session.get(InboundHospital, call_id)
    vapi_id = _first(payload, "vapi_id", "call_id")
    if vapi_id:
        return (
            session.query(InboundHospital)
            .filter(InboundHospital.vapi_id == str(vapi_id))
            .one_or_none()
        )
    task_id = payload.get("task_id")
    if task_id:
        return (
            session.query(InboundHospital)
            .filter(InboundHospital.task_id == str(task_id))
            .one_or_none()
        )
    return None
