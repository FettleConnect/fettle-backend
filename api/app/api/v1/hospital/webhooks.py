from __future__ import annotations

"""
Webhook handlers for LiveKit and Vobiz.

LiveKit Webhook (POST /api/v1/webhooks/livekit):
  - room_finished → triggers process_outbound_calls or process_inbound_calls task

Vobiz Inbound Webhook (POST /api/v1/webhooks/vobiz/inbound):
  - Maps 'To' DID to organization via HospitalConfig.vobiz_did (NO hardcoded numbers).
  - Creates or updates InboundCall record.

Vobiz Outbound Webhook (POST /api/v1/webhooks/vobiz/outbound):
  - Updates OutboundCall status via correlation ID.

Vobiz WhatsApp Webhook (POST /api/v1/webhooks/vobiz/whatsapp):
  - Logs delivery status and incoming messages.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db.session import get_async_session
from api.app.models.hospital.call import InboundCall, OutboundCall
from api.app.models.organization import HospitalConfig

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

_VOBIZ_STATUS_MAP = {
    "completed": ("ended", "connected"),
    "busy": ("failed", "not_connected"),
    "no-answer": ("failed", "not_connected"),
    "failed": ("failed", "not_connected"),
    "cancel": ("failed", "not_connected"),
}


@router.post("/livekit")
async def livekit_webhook(
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Handles LiveKit room_finished events.
    Decrements the active call counter and dispatches the appropriate Celery
    processing task (inbound vs outbound detected from room name prefix).
    """
    try:
        event = await request.json()
    except Exception:
        event = {}

    event_type = event.get("event")
    room_name = event.get("room", {}).get("name", "")

    logger.info("LiveKit webhook: %s | room=%s", event_type, room_name)

    if event_type == "room_finished" and room_name:
        background.add_task(_handle_room_finished, room_name, session)

    return {"status": "received"}


async def _handle_room_finished(room_name: str, session: AsyncSession) -> None:
    import redis as _redis

    from api.app.core.config import settings

    # Decrement concurrency counter (self-healing: clamp to 0)
    try:
        r = _redis.StrictRedis.from_url(settings.redis_url, decode_responses=True)
        new_count = r.decr("active_outbound_calls_count")
        if new_count < 0:
            r.set("active_outbound_calls_count", 0)
    except Exception as exc:
        logger.warning("Redis decrement failed: %s", exc)

    from api.app.worker.tasks import (process_inbound_calls,
                                      process_outbound_calls)

    if room_name.startswith("inbound_"):
        process_inbound_calls.delay({"vapi_id": room_name})
        logger.info("Inbound processing triggered: %s", room_name)
    else:
        # Look up the outbound call record for this room
        call = await session.scalar(
            select(OutboundCall).where(OutboundCall.livekit_call_id == room_name)
        )
        if call:
            process_outbound_calls.delay(
                {
                    "id": str(call.id),
                    "vapi_id": room_name,
                    "patient_id": str(call.patient_id) if call.patient_id else None,
                }
            )
            logger.info("Outbound processing triggered: %s", room_name)
        else:
            logger.warning("No outbound call record for room: %s", room_name)


@router.post("/vobiz/inbound")
@router.post("/vobiz/inbound/")
async def vobiz_inbound_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Receives Vobiz SIP inbound events. Maps the 'To' DID to an organization via
    HospitalConfig.vobiz_did — no phone numbers are hardcoded here.
    Creates/updates an InboundCall record.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    call_uuid = data.get("CallUUID")
    call_status = data.get("CallStatus", "ringing")
    from_num = data.get("From", "")
    to_num = data.get("To", "")

    logger.info(
        "Vobiz inbound: UUID=%s Status=%s From=%s To=%s",
        call_uuid,
        call_status,
        from_num,
        to_num,
    )

    if not call_uuid:
        return {"status": "acknowledged", "msg": "no CallUUID"}

    # Resolve organization from the DID (database-driven, no hardcoded numbers)
    org_id: uuid.UUID | None = None
    if to_num:
        cfg = await session.scalar(
            select(HospitalConfig).where(HospitalConfig.vobiz_did == to_num.strip())
        )
        if cfg:
            org_id = cfg.organization_id
        else:
            # Try matching last 10 digits
            suffix = to_num[-10:] if len(to_num) >= 10 else to_num
            cfg = await session.scalar(
                select(HospitalConfig).where(
                    HospitalConfig.vobiz_did.like(f"%{suffix}")
                )
            )
            if cfg:
                org_id = cfg.organization_id

    if org_id is None:
        logger.info("Inbound: unknown DID %s — no matching HospitalConfig", to_num)
        return {"status": "acknowledged", "msg": "unknown DID"}

    calling_process = (
        "connected" if call_status in ("completed", "answered") else "not_happened"
    )

    # Upsert InboundCall record
    existing = await session.scalar(
        select(InboundCall).where(InboundCall.livekit_call_id == call_uuid)
    )
    if existing:
        existing.status = call_status
        existing.calling_process = calling_process
    else:
        session.add(
            InboundCall(
                id=uuid.uuid4(),
                organization_id=org_id,
                livekit_call_id=call_uuid,
                status=call_status,
                from_phone_number=from_num,
                to_phone_number=to_num,
                calling_process=calling_process,
                started_at=datetime.now(tz=timezone.utc),
            )
        )

    await session.flush()  # Ensure record is visible in the same session/transaction
    return {"status": "acknowledged"}


@router.post("/vobiz/outbound")
@router.post("/vobiz/outbound/")
async def vobiz_outbound_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Receives Vobiz SIP outbound status updates.
    Correlation ID = livekit_call_id (room name) passed in X-VH-CorrelationId SIP header.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    call_status = data.get("CallStatus", "")
    custom_headers = data.get("CustomSIPHeaders", {})
    correlation_id = custom_headers.get("X-VH-CorrelationId")

    logger.info(
        "Vobiz outbound: Status=%s CorrelationId=%s", call_status, correlation_id
    )

    if correlation_id:
        db_status, calling_process = _VOBIZ_STATUS_MAP.get(call_status, (None, None))
        if db_status:
            await session.execute(
                update(OutboundCall)
                .where(OutboundCall.livekit_call_id == correlation_id)
                .values(
                    status=db_status,
                    calling_process=calling_process,
                    ended_reason=data.get("HangupCauseName", call_status),
                )
            )

    return {"status": "acknowledged"}


@router.post("/vobiz/whatsapp")
@router.post("/vobiz/whatsapp/")
async def vobiz_whatsapp_webhook(request: Request) -> dict:
    """
    Handles Vobiz WhatsApp status/incoming message events.
    Logs for observability — no business logic required at this stage.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    event = data.get("event")
    channel_id = data.get("channel_id")

    if event == "message.status":
        msg_data = data.get("data", {})
        logger.info(
            "WhatsApp status: channel=%s msg_id=%s status=%s to=%s",
            channel_id,
            msg_data.get("message_id"),
            msg_data.get("status"),
            msg_data.get("to"),
        )
    elif event == "message.received":
        from_num = data.get("data", {}).get("from")
        text = data.get("data", {}).get("text", {}).get("body")
        logger.info("WhatsApp received: from=%s text=%s", from_num, text)

    return {"status": "acknowledged"}
