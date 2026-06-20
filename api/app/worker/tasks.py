from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta, timezone
from typing import Any, cast

import boto3
from asgiref.sync import async_to_sync
from sqlalchemy import select, update

from api.app.celery_app import celery_app  # noqa: F401 (registers tasks on import)
from api.app.core.config import settings
from api.app.db.base import AsyncSessionLocal
from api.app.services.llm import llm_client, openrouter_model
import redis as _redis_lib

logger = logging.getLogger(__name__)

_openrouter_client = llm_client()

_redis_host = (
    settings.redis_url.split("//")[-1].split(":")[0]
    if "//" in settings.redis_url
    else "redis"
)
_redis_port = (
    int(settings.redis_url.split(":")[-1].split("/")[0])
    if ":" in settings.redis_url
    else 6379
)

redis_client = _redis_lib.StrictRedis(
    host=_redis_host, port=_redis_port, db=0, decode_responses=True
)
CONCURRENCY_LIMIT = settings.call_concurrency_limit

# ── Room name helpers ─────────────────────────────────────────────────────────


def _is_inbound_room(room_name: str) -> bool:
    return room_name.startswith("inbound_")


def build_room_metadata(
    organization_id: str,
    hospital_name: str,
    agent_prompt_prefix: str = "",
    call_type: str = "outbound",
    phone_number: str = "",
    patient_id: str | None = None,
    campaign_id: str | None = None,
    prompt: str = "",
) -> str:
    """Returns JSON string to embed in LiveKit Room Metadata."""
    return json.dumps(
        {
            "organization_id": organization_id,
            "hospital_name": hospital_name,
            "agent_prompt_prefix": agent_prompt_prefix,
            "type": call_type,
            "phone_number": phone_number,
            "patient_id": patient_id or "",
            "campaign_id": campaign_id or "",
            "prompt": prompt,
        }
    )


# ── AI transcript analysis ────────────────────────────────────────────────────


def json_audio(patient_id: str, text: str, called_at: str, duration: float) -> dict:
    """
    Send transcript to OpenRouter for structured feedback analysis.
    Returns a dict matching CallFeedback fields.
    """
    system_prompt = """
    You are a hospital call analysis agent. Analyze call transcripts between hospital AI agents and patients.

    IMPORTANT — Language Policy:
    The conversations are primarily in English, Hindi, or Telugu.
    If another language is detected, set escalation_required: true and note it in remarks.

    Return ONLY valid JSON in this exact format:
    {
      "call_status": "connected" or "not_connected",
      "call_outcome": "positive" or "negative" or "escalated" or "no_feedback",
      "remarks": "brief summary",
      "issue_description": "detail if escalation_required",
      "called_by": "CloudConnect Agent",
      "community_added": false,
      "revisit_encouraged": false,
      "escalation_required": false
    }
    """
    try:
        response = _openrouter_client.chat.completions.create(
            model=openrouter_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Analyze transcript:\n{text}\ncalled_at: {called_at}",
                },
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
        parsed["patient_id"] = patient_id
        parsed["called_at"] = called_at
        parsed["call_duration"] = duration
        return parsed
    except Exception as exc:
        logger.exception("json_audio failed: %s", exc)
        return {"error": 1, "msg": str(exc)}


# ── S3 helpers ────────────────────────────────────────────────────────────────


def read_from_s3(bucket: str, key: str) -> dict:
    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        return {
            "error": 0,
            "text": s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode(),
        }
    except Exception as exc:
        return {"error": 1, "errorMsg": str(exc)}


# ── Async DB helpers ──────────────────────────────────────────────────────────


async def _get_campaign(campaign_id: str):
    from api.app.models.hospital.campaign import Campaign

    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(Campaign).where(Campaign.id == uuid.UUID(campaign_id))
        )


async def _get_org_and_config(organization_id: str):
    from api.app.models.organization import HospitalConfig, Organization

    async with AsyncSessionLocal() as session:
        org = await session.scalar(
            select(Organization).where(Organization.id == uuid.UUID(organization_id))
        )
        cfg = await session.scalar(
            select(HospitalConfig).where(
                HospitalConfig.organization_id == uuid.UUID(organization_id)
            )
        )
        return org, cfg


async def _get_patient(patient_id: str):
    from api.app.models.hospital.patient import Patient

    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(Patient).where(Patient.id == uuid.UUID(patient_id))
        )


async def _get_campaign_patients(campaign_id: str, organization_id: str) -> list:
    from api.app.models.hospital.call import OutboundCall
    from api.app.models.hospital.campaign import Campaign
    from api.app.models.hospital.patient import Patient

    async with AsyncSessionLocal() as session:
        campaign = await session.scalar(
            select(Campaign).where(Campaign.id == uuid.UUID(campaign_id))
        )
        if not campaign:
            return []

        org_id = uuid.UUID(organization_id)
        q = select(Patient).where(Patient.organization_id == org_id)

        if campaign.audience_type == "past_data":
            if campaign.start_date and campaign.end_date:
                from sqlalchemy import func

                q = q.where(
                    func.date(Patient.uploaded_at).between(
                        campaign.start_date, campaign.end_date
                    )
                )
        elif campaign.audience_type == "upload":
            if campaign.patient_ids:
                pids = [uuid.UUID(pid) for pid in campaign.patient_ids]
                q = q.where(Patient.id.in_(pids))

        if campaign.unconnected_only:
            ever_connected = select(OutboundCall.patient_id).where(
                OutboundCall.organization_id == org_id,
                OutboundCall.calling_process == "connected",
            )
            q = q.where(Patient.id.notin_(ever_connected))

        patients = (await session.execute(q)).scalars().all()
        return [(p.id, p.mobile_no, campaign.purpose or "") for p in patients]


async def _check_already_called(campaign_id: str, patient_id: uuid.UUID) -> bool:
    from api.app.models.hospital.call import OutboundCall

    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(OutboundCall).where(
                OutboundCall.campaign_id == uuid.UUID(campaign_id),
                OutboundCall.patient_id == patient_id,
            )
        )
        return row is not None


async def _create_outbound_call(
    organization_id: str,
    patient_id: str | None,
    campaign_id: str | None,
    room_id: str,
) -> str:
    from api.app.models.hospital.call import OutboundCall

    async with AsyncSessionLocal() as session:
        call = OutboundCall(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(organization_id),
            patient_id=uuid.UUID(patient_id) if patient_id else None,
            campaign_id=uuid.UUID(campaign_id) if campaign_id else None,
            livekit_call_id=room_id,
            status="queued",
            calling_process="queued",
        )
        session.add(call)
        await session.commit()
        return str(call.id)


async def _update_outbound_call(call_id: str, **fields) -> None:
    from api.app.models.hospital.call import OutboundCall

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(OutboundCall)
            .where(OutboundCall.id == uuid.UUID(call_id))
            .values(**fields)
        )
        await session.commit()


async def _get_outbound_call_by_room(room_id: str):
    from api.app.models.hospital.call import OutboundCall

    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(OutboundCall).where(OutboundCall.livekit_call_id == room_id)
        )


async def _get_inbound_call_by_room(room_id: str):
    from api.app.models.hospital.call import InboundCall

    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(InboundCall).where(InboundCall.livekit_call_id == room_id)
        )


async def _create_call_feedback(
    organization_id: str,
    patient_id: str,
    ai_result: dict,
    call_duration: float,
    called_at_str: str,
) -> None:
    from api.app.models.hospital.call import CallFeedback, Escalation
    from api.app.models.hospital.patient import Patient

    async with AsyncSessionLocal() as session:
        try:
            called_at = datetime.fromisoformat(called_at_str).replace(
                tzinfo=timezone.utc
            )
        except Exception:
            called_at = datetime.now(tz=timezone.utc)

        fb = CallFeedback(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(organization_id),
            patient_id=uuid.UUID(patient_id),
            call_status="connected",
            call_outcome=ai_result.get("call_outcome", "no_feedback"),
            remarks=ai_result.get("remarks", ""),
            community_added=ai_result.get("community_added", False),
            revisit_encouraged=ai_result.get("revisit_encouraged", False),
            escalation_required=ai_result.get("escalation_required", False),
            call_duration=int(call_duration * 60),
            called_by="CloudConnect Agent",
            called_at=called_at,
        )
        session.add(fb)

        if ai_result.get("escalation_required"):
            patient = await session.scalar(
                select(Patient).where(Patient.id == uuid.UUID(patient_id))
            )
            session.add(
                Escalation(
                    id=uuid.uuid4(),
                    organization_id=uuid.UUID(organization_id),
                    patient_id=uuid.UUID(patient_id),
                    issue_description=ai_result.get(
                        "remarks", "Agent-detected escalation"
                    ),
                    department=patient.department if patient else None,
                )
            )

        await session.commit()


async def _create_inbound_feedback(
    organization_id: str,
    inbound_call_id: str,
    ai_result: dict,
    call_duration: float,
) -> None:
    from api.app.models.hospital.call import (
        InboundCall,
        InboundCommunityEngagement,
        InboundEscalation,
        InboundFeedback,
    )

    async with AsyncSessionLocal() as session:
        inbound_id = uuid.UUID(inbound_call_id)
        org_id = uuid.UUID(organization_id)
        call = await session.scalar(
            select(InboundCall).where(InboundCall.id == inbound_id)
        )

        session.add(
            InboundFeedback(
                id=uuid.uuid4(),
                organization_id=org_id,
                inbound_call_id=inbound_id,
                call_outcome=ai_result.get("call_outcome", "no_feedback"),
                remarks=ai_result.get("remarks", ""),
                community_added=ai_result.get("community_added", False),
                revisit_encouraged=ai_result.get("revisit_encouraged", False),
                escalation_required=ai_result.get("escalation_required", False),
                call_duration=int(call_duration * 60),
                called_by="Fettle Inbound Agent",
            )
        )

        session.add(
            InboundCommunityEngagement(
                id=uuid.uuid4(),
                organization_id=org_id,
                inbound_call_id=inbound_id,
                engagement_type=ai_result.get("call_outcome", "general_query"),
                department=call.department if call else "General",
            )
        )

        if ai_result.get("escalation_required"):
            session.add(
                InboundEscalation(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    inbound_call_id=inbound_id,
                    issue_description=ai_result.get("remarks", "Inbound Escalation"),
                    department=call.department if call else None,
                )
            )

        await session.commit()


async def _update_inbound_call(call_id: str, **fields) -> None:
    from api.app.models.hospital.call import InboundCall

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(InboundCall)
            .where(InboundCall.id == uuid.UUID(call_id))
            .values(**fields)
        )
        await session.commit()


async def _get_medivoice_session(session_id: str):
    from api.app.models.hospital.medivoice import MediVoiceSession

    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(MediVoiceSession).where(MediVoiceSession.id == uuid.UUID(session_id))
        )


async def _get_user(user_id: str):
    from api.app.models.auth import User

    async with AsyncSessionLocal() as session:
        return await session.scalar(select(User).where(User.id == uuid.UUID(user_id)))


# ── Celery Tasks ──────────────────────────────────────────────────────────────


@celery_app.task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3
)
def run_campaign_task(self, campaign_id: str):
    """Orchestrate a campaign: enqueue individual call tasks respecting concurrency limit."""
    from time import sleep

    try:
        campaign = async_to_sync(_get_campaign)(campaign_id)
        if not campaign:
            return {"error": 1, "msg": f"Campaign {campaign_id} not found"}

        org_id = str(campaign.organization_id)
        patients = async_to_sync(_get_campaign_patients)(campaign_id, org_id)

        logger.info("Campaign %s: targeting %d patients", campaign.name, len(patients))

        for patient_id, mobile_no, purpose in patients:
            if async_to_sync(_check_already_called)(campaign_id, patient_id):
                continue

            # Respect concurrency ceiling
            while True:
                active = int(
                    cast(Any, redis_client.get("active_outbound_calls_count")) or 0
                )
                if active < CONCURRENCY_LIMIT:
                    break
                logger.debug(
                    "Concurrency limit (%d) reached. Waiting...", CONCURRENCY_LIMIT
                )
                sleep(10)

            redis_client.incr("active_outbound_calls_count")
            redis_client.expire("active_outbound_calls_count", 300)

            call_outbound_task.delay(
                {
                    "organization_id": org_id,
                    "patient_id": str(patient_id),
                    "campaign_id": campaign_id,
                    "prompt": purpose,
                }
            )

        return {
            "status": "completed",
            "campaign_id": campaign_id,
            "count": len(patients),
        }
    except Exception as exc:
        logger.exception("run_campaign_task error")
        return {"error": 1, "msg": str(exc)}


@celery_app.task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3
)
def call_outbound_task(self, payload: dict):
    """Create OutboundCall record and dispatch via LiveKit SIP."""
    try:
        org_id = payload["organization_id"]
        patient_id = payload.get("patient_id")
        campaign_id = payload.get("campaign_id")
        prompt = payload.get("prompt", "")

        # Fetch tenant config
        org, cfg = async_to_sync(_get_org_and_config)(org_id)
        if not org:
            return {"error": 1, "msg": "Organization not found"}

        # Fetch patient phone
        phone = None
        if patient_id:
            patient = async_to_sync(_get_patient)(patient_id)
            phone = patient.mobile_no if patient else None

        if not phone:
            return {"error": 1, "msg": "No phone number for patient"}

        if not phone.startswith("+"):
            phone = "+91" + phone.lstrip("0")

        # Build room ID
        import time

        room_id = f"out_{patient_id or org_id}_{int(time.time())}_livekit"

        # Create OutboundCall record before dispatch
        call_id = async_to_sync(_create_outbound_call)(
            org_id, patient_id, campaign_id, room_id
        )

        from api.app.services.livekit import dispatch_outbound_call_sync

        result = dispatch_outbound_call_sync(
            phone_number=phone,
            room_id=room_id,
            organization_id=org_id,
            hospital_name=org.name,
            agent_prompt_prefix=cfg.agent_prompt_prefix if cfg else "",
            vobiz_did=cfg.vobiz_did if cfg else None,
            sip_trunk_id=cfg.vobiz_sip_trunk_id if cfg else None,
            patient_id=patient_id,
            campaign_id=campaign_id,
            prompt=prompt,
        )

        if result.get("error") == 0:
            return {"error": 0, "call_id": call_id, "room_id": room_id}
        else:
            redis_client.decr("active_outbound_calls_count")
            async_to_sync(_update_outbound_call)(
                call_id, status="failed", calling_process="not_connected"
            )
            return result

    except Exception as exc:
        redis_client.decr("active_outbound_calls_count")
        logger.exception("call_outbound_task error")
        return {"error": 1, "msg": str(exc)}


@celery_app.task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3
)
def process_outbound_calls(self, json_payload: dict):
    """Pull S3 transcript, AI-analyze, create feedback records, update OutboundCall."""
    try:
        db_id = json_payload["id"]
        room_name = json_payload["vapi_id"]
        patient_id = json_payload.get("patient_id")

        s3_bucket = os.environ.get("LIVEKIT_BUCKET_NAME", settings.s3_bucket_name)

        if "transcript" in json_payload:
            transcript_data = json_payload["transcript"]
            metadata_data = json_payload.get("metadata", {})
        else:
            t_res = read_from_s3(s3_bucket, f"transcripts/{room_name}.json")
            m_res = read_from_s3(s3_bucket, f"calls/{room_name}.json")
            if t_res["error"] != 0 or m_res["error"] != 0:
                return {"error": 1, "msg": "S3 data missing"}
            transcript_data = json.loads(t_res["text"])
            metadata_data = json.loads(m_res["text"])

        started_at = metadata_data.get("dialed_at") or metadata_data.get(
            "started_at", ""
        )
        ended_at = metadata_data.get("ended_at", "")

        text_message = "\n".join(
            f"{item['role']}: {item['content'][0]}"
            for item in transcript_data.get("items", [])
            if item.get("type") == "message"
        )

        call_duration = 0.0
        if started_at and ended_at:
            try:
                call_duration = round(
                    (
                        datetime.fromisoformat(ended_at)
                        - datetime.fromisoformat(started_at)
                    ).total_seconds()
                    / 60,
                    2,
                )
            except Exception:
                pass

        ai_result = json_audio(
            patient_id or db_id, text_message, started_at, call_duration
        )

        if (
            "error" not in ai_result
            and ai_result.get("call_status") == "connected"
            and patient_id
        ):
            # Fetch organization_id from the call record
            call = async_to_sync(_get_outbound_call_by_room)(room_name)
            if call:
                async_to_sync(_create_call_feedback)(
                    str(call.organization_id),
                    patient_id,
                    ai_result,
                    call_duration,
                    started_at,
                )

        async_to_sync(_update_outbound_call)(
            db_id,
            status="ended",
            started_at=(
                datetime.fromisoformat(started_at).replace(tzinfo=timezone.utc)
                if started_at
                else None
            ),
            ended_at=(
                datetime.fromisoformat(ended_at).replace(tzinfo=timezone.utc)
                if ended_at
                else None
            ),
            message_s3_link=f"s3://{s3_bucket}/transcripts/{room_name}.json",
            audio_s3_link=f"s3://{s3_bucket}/video_record/{room_name}.ogg",
            calling_process=ai_result.get("call_status", "not_connected"),
        )
        return {"status": "success"}

    except Exception as exc:
        logger.exception("process_outbound_calls error")
        return {"error": 1, "msg": str(exc)}


@celery_app.task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3
)
def process_inbound_calls(self, json_payload: dict):
    """Pull S3 transcript for inbound call, AI-analyze, create feedback/escalation records."""
    try:
        room_name = json_payload["vapi_id"]
        s3_bucket = os.environ.get("LIVEKIT_BUCKET_NAME", settings.s3_bucket_name)

        call = async_to_sync(_get_inbound_call_by_room)(room_name)
        if not call:
            return {"error": 1, "msg": f"No inbound call record for room {room_name}"}

        if "transcript" in json_payload:
            transcript_data = json_payload["transcript"]
            metadata_data = json_payload.get("metadata", {})
        else:
            t_res = read_from_s3(s3_bucket, f"transcripts/{room_name}.json")
            m_res = read_from_s3(s3_bucket, f"calls/{room_name}.json")
            if t_res["error"] != 0 or m_res["error"] != 0:
                return {"error": 1, "msg": f"S3 data missing for {room_name}"}
            transcript_data = json.loads(t_res["text"])
            metadata_data = json.loads(m_res["text"])

        started_at = metadata_data.get("dialed_at") or metadata_data.get(
            "started_at", ""
        )
        ended_at = metadata_data.get("ended_at", "")

        text_message = "\n".join(
            f"{item['role']}: {item['content'][0]}"
            for item in transcript_data.get("items", [])
            if item.get("type") == "message"
        )

        call_duration = 0.0
        if started_at and ended_at:
            try:
                call_duration = round(
                    (
                        datetime.fromisoformat(ended_at)
                        - datetime.fromisoformat(started_at)
                    ).total_seconds()
                    / 60,
                    2,
                )
            except Exception:
                pass

        ai_result = json_audio(
            str(call.id), text_message, started_at or str(datetime.now()), call_duration
        )

        if "error" not in ai_result and ai_result.get("call_status") == "connected":
            async_to_sync(_create_inbound_feedback)(
                str(call.organization_id), str(call.id), ai_result, call_duration
            )

        async_to_sync(_update_inbound_call)(
            str(call.id),
            status="ended",
            message_s3_link=f"s3://{s3_bucket}/transcripts/{room_name}.json",
            audio_s3_link=f"s3://{s3_bucket}/video_record/{room_name}.ogg",
            calling_process=(
                "connected"
                if ai_result.get("call_status") == "connected"
                else "not_connected"
            ),
        )
        return {"status": "success", "id": str(call.id)}

    except Exception as exc:
        logger.exception("process_inbound_calls error")
        return {"error": 1, "msg": str(exc)}


@celery_app.task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3
)
def send_prescription_notifications(self, session_id: str):
    """Send MediVoice consultation summary via WhatsApp to the patient."""
    try:
        session = async_to_sync(_get_medivoice_session)(session_id)
        if not session:
            return {"error": 1, "msg": "Session not found"}

        doctor = async_to_sync(_get_user)(str(session.doctor_id))
        org, cfg = async_to_sync(_get_org_and_config)(str(session.organization_id))

        if not doctor or not session.patient_mobile:
            return {"error": 1, "msg": "Missing doctor or patient contact"}

        med_str = ""
        if session.medicines:
            parts = []
            for m in session.medicines:
                if isinstance(m, dict):
                    parts.append(
                        f"- {m.get('name')} ({m.get('dosage', '')}, {m.get('duration', '')})"
                    )
                else:
                    parts.append(f"- {m}")
            if parts:
                med_str = "\nMedicines prescribed:\n" + "\n".join(parts)

        revisit_str = ""
        if session.revisit_date:
            revisit_str = f"\nRevisit Date: {session.revisit_date}"
            if session.revisit_time:
                revisit_str += f" at {session.revisit_time}"

        message = (
            f"Hello {session.patient_name}, this is from Dr. {doctor.name}'s office at {org.name}.\n\n"
            f"Diagnosis: {session.diagnosis or 'See attached'}\n"
            f"Summary: {session.overall_summary or ''}{med_str}{revisit_str}\n\n"
            f"For follow-ups, contact: {doctor.mobile_number or org.name}."
        )

        import asyncio

        from api.app.services.whatsapp import send_whatsapp

        asyncio.get_event_loop().run_until_complete(
            send_whatsapp(
                to_number=session.patient_mobile,
                template_name="fettle_update",
                template_params=[org.name, message],
                channel_id=cfg.whatsapp_channel_id if cfg else None,
            )
        )
        return {"status": "success", "session_id": session_id}
    except Exception as exc:
        logger.exception("send_prescription_notifications error")
        return {"status": "error", "message": str(exc)}


@celery_app.task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3
)
def reminder_task(self, session_id: str, reminder_type: str):
    """Send appointment reminder via WhatsApp (24h or 1h before revisit)."""
    try:
        session = async_to_sync(_get_medivoice_session)(session_id)
        if not session:
            return {"error": 1, "msg": "Session not found"}

        doctor = async_to_sync(_get_user)(str(session.doctor_id))
        org, cfg = async_to_sync(_get_org_and_config)(str(session.organization_id))

        time_str = f"on {session.revisit_date}"
        if session.revisit_time:
            time_str += f" at {session.revisit_time}"

        msg = (
            f"Reminder ({reminder_type}): This is {org.name}. "
            f"You have a follow-up with Dr. {doctor.name} {time_str}. "
            f"Contact {doctor.mobile_number or org.name} for queries."
        )

        import asyncio

        from api.app.services.whatsapp import send_whatsapp

        asyncio.get_event_loop().run_until_complete(
            send_whatsapp(
                to_number=session.patient_mobile,
                template_name="fettle_update",
                template_params=[org.name, msg],
                channel_id=cfg.whatsapp_channel_id if cfg else None,
            )
        )
        return {"status": "success", "type": reminder_type}
    except Exception as exc:
        logger.exception("reminder_task error")
        return {"status": "error", "message": str(exc)}


@celery_app.task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3
)
def schedule_reminder_calls(self, session_id: str):
    """Schedule 24h and 1h ETA reminder tasks for a MediVoice revisit."""
    try:
        session = async_to_sync(_get_medivoice_session)(session_id)
        if not session or not session.revisit_date:
            return {"status": "skipped", "reason": "no revisit date"}

        revisit_time = session.revisit_time if session.revisit_time else dt_time(10, 0)
        revisit_dt = datetime.combine(session.revisit_date, revisit_time).replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(tz=timezone.utc)

        eta_24h = revisit_dt - timedelta(hours=24)
        if eta_24h > now:
            reminder_task.apply_async(args=[session_id, "24h"], eta=eta_24h)

        eta_1h = revisit_dt - timedelta(hours=1)
        if eta_1h > now:
            reminder_task.apply_async(args=[session_id, "1h"], eta=eta_1h)

        return {
            "status": "scheduled",
            "session_id": session_id,
            "revisit": str(revisit_dt),
        }
    except Exception as exc:
        logger.exception("schedule_reminder_calls error")
        return {"status": "error", "message": str(exc)}
