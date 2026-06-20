from __future__ import annotations

"""
Multi-tenant LiveKit SIP dispatch.

Key invariants:
- hospital_name and agent_prompt_prefix are ALWAYS read from Room Metadata, never hardcoded.
- vobiz_did is used as the outbound caller ID so patients see their hospital's official number.
- trunk lookup uses HospitalConfig.vobiz_sip_trunk_id first, falls back to name search.
"""

import json
import logging
import os

from api.app.core.config import settings

logger = logging.getLogger(__name__)

# Trunk name used when per-hospital trunk ID is not configured
_DEFAULT_OUTBOUND_TRUNK_NAME = "Vobiz_Outbound_V5"


def _livekit_url() -> str:
    """Pick internal URL inside Docker, external URL on host."""
    if os.path.exists("/.dockerenv"):
        return os.environ.get("LIVEKIT_URL_INTERNAL", "http://livekit-server:7880")
    return settings.livekit_url or "http://127.0.0.1:7880"


async def dispatch_outbound_call(
    *,
    phone_number: str,
    room_id: str,
    organization_id: str,
    hospital_name: str,
    agent_prompt_prefix: str = "",
    vobiz_did: str | None = None,
    sip_trunk_id: str | None = None,
    patient_id: str | None = None,
    campaign_id: str | None = None,
    prompt: str = "",
) -> dict:
    """
    Dispatch the LiveKit AI agent to a room and invite the patient via SIP.

    :param vobiz_did: Caller ID shown to the patient (the hospital's official DID).
                     When set, passed as SIP From header for caller masking.
    :param sip_trunk_id: Pre-resolved trunk ID from HospitalConfig; avoids trunk name lookup.
    """
    try:
        from livekit import api as lk_api

        url = _livekit_url()
        api_key = settings.livekit_api_key
        api_secret = settings.livekit_api_secret

        # Ensure phone number is E.164
        if not phone_number.startswith("+"):
            phone_number = "+91" + phone_number.lstrip("0")

        metadata = json.dumps(
            {
                "organization_id": organization_id,
                "hospital_name": hospital_name,
                "agent_prompt_prefix": agent_prompt_prefix,
                "type": "outbound",
                "phone_number": phone_number,
                "patient_id": patient_id or "",
                "campaign_id": campaign_id or "",
                "prompt": prompt,
            }
        )

        async with lk_api.LiveKitAPI(url, api_key, api_secret) as lk:
            # 1. Resolve outbound SIP trunk
            outbound_trunk_id = sip_trunk_id
            if not outbound_trunk_id:
                trunks = await lk.sip.list_outbound_trunk(
                    lk_api.ListSIPOutboundTrunkRequest()
                )
                target = next(
                    (t for t in trunks.items if t.name == _DEFAULT_OUTBOUND_TRUNK_NAME),
                    None,
                )
                if not target:
                    return {
                        "error": 1,
                        "msg": f"Trunk '{_DEFAULT_OUTBOUND_TRUNK_NAME}' not found",
                    }
                outbound_trunk_id = target.sip_trunk_id

            logger.info("Dispatching agent to room %s for %s", room_id, hospital_name)

            # 2. Dispatch agent to room
            await lk.agent_dispatch.create_dispatch(
                lk_api.CreateAgentDispatchRequest(
                    agent_name="my-agent",
                    room=room_id,
                    metadata=metadata,
                )
            )

            # 3. Invite SIP participant — use vobiz_did as caller ID when available
            sip_request = lk_api.CreateSIPParticipantRequest(
                sip_trunk_id=outbound_trunk_id,
                sip_call_to=phone_number,
                room_name=room_id,
                participant_identity=f"sip_{phone_number}",
                participant_name="Patient",
            )
            # Pass caller DID via SIP headers for outbound masking
            if vobiz_did:
                sip_request.sip_headers = {"X-Caller-DID": vobiz_did}

            await lk.sip.create_sip_participant(sip_request)
            logger.info(
                "Outbound SIP call initiated: %s → %s",
                vobiz_did or "default",
                phone_number,
            )
            return {"error": 0, "msg": "Dispatched"}

    except Exception as exc:
        logger.exception("LiveKit dispatch failed")
        return {"error": 1, "msg": str(exc)}


def dispatch_outbound_call_sync(
    *,
    phone_number: str,
    room_id: str,
    organization_id: str,
    hospital_name: str,
    **kwargs,
) -> dict:
    """Synchronous wrapper for use in Celery tasks (no running event loop)."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(
        dispatch_outbound_call(
            phone_number=phone_number,
            room_id=room_id,
            organization_id=organization_id,
            hospital_name=hospital_name,
            **kwargs,
        )
    )


async def ensure_infrastructure(
    organization_id: str,
    hospital_name: str,
    vobiz_did: str,
    vobiz_sip_trunk_address: str | None = None,
    sip_trunk_id: str | None = None,
) -> None:
    """
    Idempotent SIP infrastructure provisioning for a single tenant.
    Creates/verifies:
    - Inbound dispatch rule routing {vobiz_did} → inbound_{did}_* rooms with org metadata.
    - (Outbound trunk is shared; per-tenant caller ID is set per-call via SIP headers.)
    Called during hospital onboarding or as a self-healing guard.
    """
    try:
        from livekit import api as lk_api

        url = _livekit_url()
        async with lk_api.LiveKitAPI(
            url, settings.livekit_api_key, settings.livekit_api_secret
        ) as lk:
            # --- Inbound Trunk ---
            inbound_trunks = await lk.sip.list_inbound_trunk(
                lk_api.ListSIPInboundTrunkRequest()
            )
            trunk_name = f"Inbound_{vobiz_did.replace('+', '')}"
            existing_trunk = next(
                (t for t in inbound_trunks.items if t.name == trunk_name), None
            )

            if not existing_trunk:
                logger.info(
                    "Guard: Creating inbound trunk for %s (%s)",
                    hospital_name,
                    vobiz_did,
                )
                created = await lk.sip.create_inbound_trunk(
                    lk_api.CreateSIPInboundTrunkRequest(
                        trunk=lk_api.SIPInboundTrunkInfo(
                            name=trunk_name,
                            numbers=[vobiz_did],
                        )
                    )
                )
                inbound_trunk_id = created.sip_trunk_id
            else:
                inbound_trunk_id = existing_trunk.sip_trunk_id

            # --- Dispatch Rule ---
            rules = await lk.sip.list_dispatch_rule(lk_api.ListSIPDispatchRuleRequest())
            rule_name = f"Route_{vobiz_did.replace('+', '')}"

            if not any(r.name == rule_name for r in rules.items):
                logger.info("Guard: Creating dispatch rule for %s", vobiz_did)
                room_metadata = json.dumps(
                    {
                        "organization_id": organization_id,
                        "hospital_name": hospital_name,
                        "type": "inbound",
                    }
                )
                await lk.sip.create_dispatch_rule(
                    lk_api.CreateSIPDispatchRuleRequest(
                        name=rule_name,
                        trunk_ids=[inbound_trunk_id],
                        rule=lk_api.SIPDispatchRule(
                            dispatch_rule_individual=lk_api.SIPDispatchRuleIndividual(
                                room_prefix=f"inbound_{vobiz_did.replace('+', '')}_",
                            )
                        ),
                        room_config=lk_api.RoomConfiguration(
                            metadata=room_metadata,
                            agents=[lk_api.RoomAgentDispatch(agent_name="my-agent")],
                        ),
                    )
                )
            logger.info("Guard: Infrastructure healthy for %s", hospital_name)
    except Exception as exc:
        logger.error("Guard failed for %s: %s", hospital_name, exc)
