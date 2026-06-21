from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.celery_app import celery_app
from app.db import SessionLocal
from app.livekit_client import dispatch_call_sync
from app.models import OutboundHospital, PatientModel
from app.tasks.analysis import json_audio


@celery_app.task(name="call_outbound_task")
def call_outbound_task(json_payload: dict[str, Any]) -> dict[str, Any]:
    session = SessionLocal()
    try:
        patient = _patient_from_payload(session, json_payload)
        phone = _first(json_payload, "phone", "phone_number", "mobile_no") or (
            patient.mobile_no if patient else None
        )
        call = OutboundHospital(
            patient_id=(
                patient.id if patient else _uuid_or_none(json_payload.get("patient_id"))
            ),
            campaign_id=_uuid_or_none(json_payload.get("campaign_id")),
            assistant_id=_uuid_or_none(json_payload.get("assistant_id")),
            task_id=json_payload.get("task_id"),
            calling_process="queued",
        )
        session.add(call)
        session.commit()

        dispatch_result: dict[str, Any] = {
            "status": "skipped",
            "reason": "missing_phone",
        }
        if phone:
            try:
                dispatch_result = dispatch_call_sync(
                    str(phone),
                    str(call.id),
                    {"outbound_call_id": str(call.id), **json_payload},
                )
                call.vapi_id = (
                    str(_first(dispatch_result, "call_id", "id", "dispatch_id") or "")
                    or call.vapi_id
                )
                call.status = (
                    str(dispatch_result.get("status"))
                    if dispatch_result.get("status") is not None
                    else call.status
                )
                call.calling_process = "dispatched"
            except Exception as exc:
                dispatch_result = {"status": "error", "error": str(exc)}
                call.status = "dispatch_failed"
                call.calling_process = "failed"
            session.commit()

        return {
            "task": "call_outbound_task",
            "id": str(call.id),
            "dispatch": dispatch_result,
            "status": call.calling_process,
        }
    finally:
        session.close()


@celery_app.task(name="process_outbound_calls")
def process_outbound_calls(json_payload: dict[str, Any]) -> dict[str, Any]:
    session = SessionLocal()
    try:
        call = _find_outbound_call(session, json_payload)
        if call is None:
            return {"task": "process_outbound_calls", "status": "call_not_found"}

        transcript = str(_first(json_payload, "transcript", "text", "summary") or "")
        analysis = json_audio(
            str(call.patient_id or ""),
            transcript,
            _datetime_or_none(_first(json_payload, "called_at", "ended_at", "endedAt")),
            _first(json_payload, "duration", "call_duration"),
        )
        call.status = json_payload.get("status", call.status)
        call.endedReason = (
            _first(json_payload, "endedReason", "ended_reason") or call.endedReason
        )
        call.ended_at = (
            _datetime_or_none(_first(json_payload, "ended_at", "endedAt"))
            or call.ended_at
        )
        call.message_s3_link = (
            _first(json_payload, "message_s3_link", "transcript_url")
            or call.message_s3_link
        )
        call.audio_link = (
            _first(json_payload, "audio_link", "recording_url", "audio_url")
            or call.audio_link
        )
        call.task_id_process = json_payload.get("task_id_process", call.task_id_process)
        call.calling_process = "processed"
        session.commit()
        return {
            "task": "process_outbound_calls",
            "id": str(call.id),
            "analysis": analysis,
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


def _patient_from_payload(session, payload: dict[str, Any]) -> PatientModel | None:
    patient_id = _uuid_or_none(payload.get("patient_id"))
    if patient_id:
        return session.get(PatientModel, patient_id)
    phone = _first(payload, "phone", "phone_number", "mobile_no")
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    suffix = digits[-10:] if digits else str(phone)
    return (
        session.query(PatientModel)
        .filter(PatientModel.mobile_no.contains(suffix))
        .first()
    )


def _find_outbound_call(session, payload: dict[str, Any]) -> OutboundHospital | None:
    call_id = _uuid_or_none(payload.get("id") or payload.get("outbound_id"))
    if call_id:
        return session.get(OutboundHospital, call_id)
    vapi_id = _first(payload, "vapi_id", "call_id")
    if vapi_id:
        return (
            session.query(OutboundHospital)
            .filter(OutboundHospital.vapi_id == str(vapi_id))
            .one_or_none()
        )
    task_id = payload.get("task_id")
    if task_id:
        return (
            session.query(OutboundHospital)
            .filter(OutboundHospital.task_id == str(task_id))
            .one_or_none()
        )
    return None
