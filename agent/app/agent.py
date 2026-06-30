from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Any, cast

import httpx
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    InterruptionOptions,
    JobContext,
    JobExecutorType,
    JobProcess,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.llm.tool_context import ToolFlag
from livekit.plugins import cartesia, openai, silero, soniox
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()
logger = logging.getLogger("fettle-agent")

_THINKING_FILLERS = ["Uhm...", "Hmm...", "Mm-hm...", "Right...", "Okay..."]

INBOUND_GREETING_TEMPLATE = (
    "Hello, this is the {hospital_name} front desk. How can I help today?"
)
OUTBOUND_GREETING_TEMPLATE = (
    "Hello, this is calling from {hospital_name}. Do you have a minute?"
)
NAME_FALLBACK_TEMPLATE = "Hello, this is the {hospital_name} front desk. Could you tell me your name, please?"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ── Internal API helpers ───────────────────────────────────────────────────────


def _internal_api_base() -> str:
    return os.environ.get("INTERNAL_API_BASE_URL", "http://api:8000").rstrip("/")


def _internal_secret() -> str:
    return os.environ.get("INTERNAL_API_SHARED_SECRET", "")


def _openrouter_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "")


def _openrouter_model() -> str:
    return os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o")


async def _fetch_doctor_schedule(organization_id: str) -> list[dict]:
    """Fetch doctor schedule from FastAPI for the given organization."""
    secret = _internal_secret()
    if not secret or not organization_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_internal_api_base()}/api/v1/tenant/doctors",
                params={"organization_id": organization_id},
                headers={"X-Internal-API-Secret": secret},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Doctor schedule fetch failed: %s", exc)
        return []


async def _fetch_patient_info(phone_number: str) -> dict | None:
    """Look up patient by phone number via the internal patient lookup API."""
    secret = _internal_secret()
    if not secret or not phone_number:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{_internal_api_base()}/api/patient_lookup_by_phone/",
                params={"phone_number": phone_number},
                headers={"X-Internal-API-Secret": secret},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Patient lookup failed: %s", exc)
        return None


async def _raise_escalation(
    organization_id: str,
    issue_description: str,
    department: str = "",
    patient_id: str | None = None,
    phone_number: str = "",
) -> str:
    """
    Create an escalation record via POST /api/internal/escalation/.
    Protected by X-Internal-API-Secret — no JWT needed from the agent.
    """
    secret = _internal_secret()
    if not secret:
        return "Escalation failed: internal secret not configured"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_internal_api_base()}/api/internal/escalation/",
                headers={
                    "X-Internal-API-Secret": secret,
                    "Content-Type": "application/json",
                },
                json={
                    "patient_id": patient_id,
                    "phone_number": phone_number,
                    "issue_description": issue_description,
                    "department": department,
                },
            )
            if resp.status_code == 200 and resp.json().get("error") == 0:
                return "Escalation recorded"
            return f"Escalation failed: {resp.status_code}"
    except Exception as exc:
        return f"Escalation failed: {exc}"


async def _send_whatsapp(
    to_number: str,
    msg: str | None = None,
    template_name: str | None = None,
    template_params: list[str] | None = None,
    channel_id: str | None = None,
) -> bool:
    vobiz_url = os.environ.get("VOBIZ_API_URL", "https://api.vobiz.ai")
    vobiz_token = os.environ.get("VOBIZ_AUTH_TOKEN", "")
    vobiz_id = os.environ.get("VOBIZ_AUTH_ID", "")
    headers = {
        "X-Auth-ID": vobiz_id,
        "X-Auth-Token": vobiz_token,
        "Content-Type": "application/json",
    }

    import re

    digits = re.sub(r"\D", "", to_number)
    if not digits.startswith("91") and len(digits) == 10:
        digits = "91" + digits
    to = "+" + digits

    if template_name:
        components = []
        if template_params:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": p} for p in template_params
                    ],
                }
            )
        payload: dict = {
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en_US"},
                "components": components,
            },
        }
    else:
        payload = {"to": to, "type": "text", "text": {"body": msg or ""}}

    if channel_id:
        payload["channel_id"] = channel_id

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{vobiz_url}/api/v1/messaging/messages", json=payload, headers=headers
            )
            return True
    except Exception as exc:
        logger.warning("WhatsApp send failed: %s", exc)
        return False


# ── Greeting helpers ──────────────────────────────────────────────────────────


def _build_inbound_greeting(hospital_name: str, patient_payload: dict | None) -> str:
    generic = NAME_FALLBACK_TEMPLATE.format(hospital_name=hospital_name)
    if not patient_payload or patient_payload.get("error") != 0:
        return generic

    records = patient_payload.get("data") or []
    if not records:
        return generic

    patient = records[0]
    patient_name = (patient.get("patient_name") or "there").strip() or "there"
    prev = patient.get("previous_appointments") or []
    if prev:
        dept = (prev[0].get("department") or "").strip()
        if dept:
            return f"Welcome {patient_name}. I see you last visited our {dept} department. How can I help you today?"
    return f"Welcome {patient_name}. How can I help you today?"


# ── Agent classes ─────────────────────────────────────────────────────────────


class _FillerMixin:
    """Shared filler-audio caching and on-turn-complete logic."""

    _filler_audio_cache: dict[str, list[rtc.AudioFrame]]

    async def _cache_fillers(self, tts) -> None:
        if tts is None or not hasattr(tts, "synthesize"):
            return
        for filler in _THINKING_FILLERS:
            if filler in self._filler_audio_cache:
                continue
            frames: list[rtc.AudioFrame] = []
            async for output in tts.synthesize(filler):
                frames.append(output.frame)
            self._filler_audio_cache[filler] = frames

    async def _play_filler(self) -> None:
        filler = random.choice(_THINKING_FILLERS)
        if filler in self._filler_audio_cache:

            async def _gen():
                for frame in self._filler_audio_cache[filler]:
                    yield frame

            cast(Any, self).session.say(filler, audio=_gen(), add_to_chat_ctx=False)
        else:
            cast(Any, self).session.say(filler, add_to_chat_ctx=False)


class InboundAssistant(_FillerMixin, Agent):
    def __init__(
        self,
        instructions: str,
        caller_number: str | None = None,
        organization_id: str = "",
        whatsapp_channel_id: str | None = None,
    ) -> None:
        self.caller_number = caller_number
        self.organization_id = organization_id
        self.whatsapp_channel_id = whatsapp_channel_id
        self._filler_audio_cache: dict[str, list[rtc.AudioFrame]] = {}
        super().__init__(
            instructions=instructions, tools=[EndCallTool(delete_room=True).tools[0]]
        )

    async def on_enter(self) -> None:
        await self._cache_fillers(getattr(self, "tts", None))

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        await self._play_filler()

    @function_tool(
        flags=ToolFlag.IGNORE_ON_ENTER,
        description="Get doctor timings, departments, consultation fees, and OPD schedules.",
    )
    async def get_doctor_schedule(
        self, context: RunContext, doctor_name: str = "", department: str = ""
    ) -> str:
        doctors = await _fetch_doctor_schedule(self.organization_id)
        dn = doctor_name.lower().strip()
        dp = department.lower().strip()
        matches = [
            d["doctor_name"]
            for d in doctors
            if (dn and dn in d["doctor_name"].lower())
            or (dp and dp in d.get("department", "").lower())
        ]
        unique = list(dict.fromkeys(matches[:5]))
        return ", ".join(unique) if unique else "No matching doctor found."

    @function_tool(
        flags=ToolFlag.IGNORE_ON_ENTER,
        description="Send WhatsApp appointment confirmation ONLY after appointment is fully confirmed.",
    )
    async def send_appointment_confirmation(
        self,
        context: RunContext,
        patient_name: str,
        doctor_name: str,
        department: str,
        appointment_date: str,
        appointment_time: str,
    ) -> str:
        # Derive hospital name from instructions (already embedded)
        hospital_name = (
            self.instructions.split(".")[0]
            .replace("You are a front-desk assistant for ", "")
            .strip()
        )
        await _send_whatsapp(
            to_number=self.caller_number or "",
            template_name="appointment_info",
            template_params=[
                patient_name,
                doctor_name,
                department,
                appointment_date,
                appointment_time,
                hospital_name,
            ],
            channel_id=self.whatsapp_channel_id,
        )
        # Notify admin
        admin_phone = os.environ.get("ADMIN_PHONE", "")
        if admin_phone:
            await _send_whatsapp(
                to_number=admin_phone,
                msg=f"New Appt: {patient_name} with {doctor_name} on {appointment_date} at {appointment_time}",
            )
        return "Sent"

    @function_tool(
        flags=ToolFlag.IGNORE_ON_ENTER,
        description="Fetch patient details by phone number. Use caller's phone first. Returns appointment history.",
    )
    async def get_patient_info_by_phone(
        self, context: RunContext, phone_number: str = ""
    ) -> str:
        lookup = phone_number.strip() or (self.caller_number or "").strip()
        if not lookup:
            return "What is your name?"
        payload = await _fetch_patient_info(lookup)
        if not payload or payload.get("error") != 0:
            return "What is your name?"
        return json.dumps(payload, indent=2, default=str)

    @function_tool(
        flags=ToolFlag.IGNORE_ON_ENTER,
        description="Raise an escalation when the caller needs urgent help or is upset.",
    )
    async def raise_escalation(
        self,
        context: RunContext,
        issue_description: str,
        department: str = "",
        patient_id: str | None = None,
        phone_number: str = "",
    ) -> str:
        phone = (phone_number or self.caller_number or "").strip()
        return await _raise_escalation(
            organization_id=self.organization_id,
            issue_description=issue_description,
            department=department,
            patient_id=patient_id,
            phone_number=phone,
        )


class OutboundAssistant(_FillerMixin, Agent):
    def __init__(
        self,
        instructions: str,
        caller_number: str | None = None,
        organization_id: str = "",
    ) -> None:
        self.caller_number = caller_number
        self.organization_id = organization_id
        self._filler_audio_cache: dict[str, list[rtc.AudioFrame]] = {}
        super().__init__(
            instructions=instructions, tools=[EndCallTool(delete_room=True).tools[0]]
        )

    async def on_enter(self) -> None:
        await self._cache_fillers(getattr(self, "tts", None))

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        await self._play_filler()

    @function_tool(
        flags=ToolFlag.IGNORE_ON_ENTER,
        description="Log call feedback outcome after the patient finishes responding.",
    )
    async def log_call_outcome(
        self, context: RunContext, feedback_summary: str, sentiment: str
    ) -> str:
        admin_phone = os.environ.get("ADMIN_PHONE", "")
        if admin_phone:
            await _send_whatsapp(
                to_number=admin_phone,
                msg=f"Outbound Feedback:\nSummary: {feedback_summary}\nSentiment: {sentiment}",
            )
        return "Logged"


# ── Agent server ──────────────────────────────────────────────────────────────

server = AgentServer(host="0.0.0.0", job_executor_type=JobExecutorType.THREAD)


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["stt"] = soniox.STT(
        api_key=os.environ.get("SONIOX_API_KEY", ""),
        params=soniox.STTOptions(language_hints=["en", "hi", "te"]),
    )
    proc.userdata["llm"] = openai.LLM(
        model=_openrouter_model(),
        base_url=OPENROUTER_BASE_URL,
        api_key=_openrouter_api_key(),
    )
    proc.userdata["tts"] = cartesia.TTS(
        model="sonic-3",
        voice=os.environ.get(
            "CARTESIA_VOICE_ID", "927c55a9-74a9-4272-871e-a559c8989abe"
        ),
    )


server.setup_fnc = prewarm


def ghost_protection(ctx: JobContext) -> None:
    @ctx.room.on("participant_disconnected")
    def _on_disconnect(participant: rtc.RemoteParticipant) -> None:
        non_agents = [
            p
            for p in ctx.room.remote_participants.values()
            if p.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
        ]
        if not non_agents:
            ctx.shutdown()


async def create_session(ctx: JobContext) -> AgentSession:
    return AgentSession(
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=ctx.proc.userdata["tts"],
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            endpointing={"mode": "fixed", "min_delay": 0.08, "max_delay": 1.0},
            interruption=InterruptionOptions(enabled=True, min_words=2),
        ),
        aec_warmup_duration=0.5,
        min_consecutive_speech_delay=0.05,
    )


@server.rtc_session(agent_name="my-agent")
async def entrypoint(ctx: JobContext) -> None:
    ghost_protection(ctx)

    # Parse metadata — ALL tenant config comes from here, never hardcoded
    room_meta = json.loads(ctx.room.metadata) if ctx.room.metadata else {}
    job_meta = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    meta = {**room_meta, **job_meta}

    organization_id: str = meta.get("organization_id", "")
    hospital_name: str = meta.get("hospital_name", "the hospital")
    agent_prompt_prefix: str = meta.get("agent_prompt_prefix", "")
    is_outbound = meta.get("type") == "outbound"

    logger.info(
        "JOB: id=%s org=%s hospital=%s type=%s room=%s",
        ctx.job.id,
        organization_id,
        hospital_name,
        "outbound" if is_outbound else "inbound",
        ctx.room.name,
    )

    if is_outbound:
        base_inst = (
            f"You are an outbound AI assistant calling from {hospital_name}. "
            f"Purpose: {meta.get('prompt', 'health campaign follow-up')}. "
            "Be concise, respectful, and speak only English, Hindi, or Telugu. "
        )
    else:
        base_inst = (
            f"You are a front-desk assistant for {hospital_name}. "
            "Support English, Hindi, Telugu. Ask one question at a time. "
            "Use get_doctor_schedule for doctor queries. "
            "If the caller is new, collect name, spelling, and location in separate turns. "
        )

    # Append custom hospital instructions
    if agent_prompt_prefix:
        instructions = (
            f"{base_inst}\n\nHospital-specific instructions:\n{agent_prompt_prefix}"
        )
    else:
        instructions = base_inst

    # Fetch hospital WhatsApp config
    whatsapp_channel_id: str | None = None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            secret = _internal_secret()
            if secret and organization_id:
                r = await client.get(
                    f"{_internal_api_base()}/api/v1/tenant/branding",
                    headers={
                        "X-Internal-API-Secret": secret,
                        "organization-id": organization_id,
                    },
                )
                if r.status_code == 200:
                    whatsapp_channel_id = r.json().get("whatsapp_channel_id")
    except Exception:
        pass

    await ctx.connect()
    participant = await ctx.wait_for_participant()
    caller_number: str = participant.attributes.get("sip.phoneNumber", "")

    session = await create_session(ctx)

    if is_outbound:
        assistant: Agent = OutboundAssistant(
            instructions, caller_number=caller_number, organization_id=organization_id
        )
        greeting = meta.get("greeting") or OUTBOUND_GREETING_TEMPLATE.format(
            hospital_name=hospital_name
        )
    else:
        assistant = InboundAssistant(
            instructions,
            caller_number=caller_number,
            organization_id=organization_id,
            whatsapp_channel_id=whatsapp_channel_id,
        )
        greeting = meta.get("greeting")
        if not greeting:
            patient_payload = None
            if caller_number:
                try:
                    patient_payload = await _fetch_patient_info(caller_number)
                except Exception:
                    pass
            greeting = _build_inbound_greeting(hospital_name, patient_payload)

    await session.start(agent=assistant, room=ctx.room)
    await asyncio.sleep(1.5)
    logger.info("Greeting: %s", greeting)
    await session.say(greeting, allow_interruptions=False)


# ── Self-healing infrastructure guard (multi-tenant) ─────────────────────────


async def infrastructure_guard() -> None:
    """
    Ensures all active hospital DIDs have inbound SIP trunks and dispatch rules.
    Runs once at agent startup. Queries the backend for all hospital configs.
    """
    secret = _internal_secret()
    if not secret:
        logger.warning("Guard skipped: INTERNAL_API_SHARED_SECRET not set")
        return

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_internal_api_base()}/api/v1/tenant/hospitals",
                headers={
                    "X-Internal-API-Secret": secret,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "Guard: could not fetch hospital list (%d)", resp.status_code
                )
                return
            hospitals = resp.json().get("data", [])
    except Exception as exc:
        logger.error("Guard: failed to fetch hospitals: %s", exc)
        return

    api_key = os.environ.get("LIVEKIT_API_KEY", "")

    if not api_key:
        logger.warning("Guard skipped: LIVEKIT_API_KEY not set")
        return

    await _ensure_outbound_infrastructure()

    ensured_routes = 0
    for hosp in hospitals:
        org_id = hosp.get("id", "")
        name = hosp.get("name", "Hospital")
        # Fetch vobiz_did for this hospital
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_internal_api_base()}/api/v1/tenant/branding",
                    headers={
                        "X-Internal-API-Secret": secret,
                        "organization-id": org_id,
                    },
                )
                if r.status_code != 200:
                    continue
                vobiz_did: str = r.json().get("vobiz_did", "")
                if not vobiz_did:
                    continue

            await _ensure_inbound_infrastructure(
                organization_id=org_id,
                hospital_name=name,
                vobiz_did=vobiz_did,
            )
            ensured_routes += 1
        except Exception as exc:
            logger.error("Guard: failed for %s: %s", name, exc)

    if ensured_routes == 0:
        await _ensure_global_inbound_dispatch_rule()


async def _ensure_outbound_infrastructure() -> None:
    """Restore the outbound trunk shape from the working legacy backend."""
    from livekit import api as lk_api

    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning("Guard skipped: LiveKit credentials not set")
        return

    livekit_url = os.environ.get(
        "LIVEKIT_URL_INTERNAL",
        os.environ.get("LIVEKIT_URL", "http://livekit-server:7880"),
    )
    trunk_name = os.environ.get(
        "LIVEKIT_SIP_OUTBOUND_TRUNK_NAME", "Vobiz_Outbound_Final_V2"
    )
    sip_config = {
        "SIP_OUTBOUND_NUMBER": os.environ.get("SIP_OUTBOUND_NUMBER", "").strip(),
        "SIP_OUTBOUND_URI": os.environ.get("SIP_OUTBOUND_URI", "").strip(),
        "SIP_OUTBOUND_USERNAME": os.environ.get("SIP_OUTBOUND_USERNAME", "").strip(),
        "SIP_OUTBOUND_PASSWORD": os.environ.get("SIP_OUTBOUND_PASSWORD", "").strip(),
    }
    missing_sip_config = [name for name, value in sip_config.items() if not value]
    if missing_sip_config:
        logger.warning(
            "Guard skipped: outbound SIP env not set: %s",
            ", ".join(missing_sip_config),
        )
        return

    async with lk_api.LiveKitAPI(livekit_url, api_key, api_secret) as lk:
        trunks = await lk.sip.list_outbound_trunk(lk_api.ListSIPOutboundTrunkRequest())
        if any(trunk.name == trunk_name for trunk in trunks.items):
            logger.info("Guard: outbound trunk is healthy")
            return

        logger.info("Guard: creating outbound trunk %s", trunk_name)
        await lk.sip.create_outbound_trunk(
            lk_api.CreateSIPOutboundTrunkRequest(
                trunk=lk_api.SIPOutboundTrunkInfo(
                    name=trunk_name,
                    address=sip_config["SIP_OUTBOUND_URI"],
                    numbers=[sip_config["SIP_OUTBOUND_NUMBER"]],
                    auth_username=sip_config["SIP_OUTBOUND_USERNAME"],
                    auth_password=sip_config["SIP_OUTBOUND_PASSWORD"],
                    transport=lk_api.SIPTransport.SIP_TRANSPORT_UDP,
                )
            )
        )
        logger.info("Guard: outbound trunk created")


async def _ensure_global_inbound_dispatch_rule() -> None:
    """Reference fallback: route any inbound SIP trunk to inbound_* rooms."""
    from livekit import api as lk_api

    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning("Guard skipped: LiveKit credentials not set")
        return

    livekit_url = os.environ.get(
        "LIVEKIT_URL_INTERNAL",
        os.environ.get("LIVEKIT_URL", "http://livekit-server:7880"),
    )
    rule_name = "Inbound_Routing_Rule"
    trunk_id = os.environ.get("LIVEKIT_SIP_TRUNK_ID", "").strip()

    async with lk_api.LiveKitAPI(livekit_url, api_key, api_secret) as lk:
        rules = await lk.sip.list_dispatch_rule(lk_api.ListSIPDispatchRuleRequest())
        if any(rule.name == rule_name for rule in rules.items):
            logger.info("Guard: global inbound dispatch rule is healthy")
            return

        logger.info("Guard: creating global inbound dispatch rule")
        request = _global_inbound_rule_request(lk_api, rule_name, trunk_id)

        try:
            await lk.sip.create_dispatch_rule(request)
        except Exception:
            if not trunk_id:
                raise
            logger.warning(
                "Guard: retrying global inbound rule without LIVEKIT_SIP_TRUNK_ID"
            )
            await lk.sip.create_dispatch_rule(
                _global_inbound_rule_request(lk_api, rule_name, "")
            )
        logger.info("Guard: global inbound dispatch rule created")


def _global_inbound_rule_request(lk_api: Any, rule_name: str, trunk_id: str):
    request = lk_api.CreateSIPDispatchRuleRequest(
        name=rule_name,
        rule=lk_api.SIPDispatchRule(
            dispatch_rule_individual=lk_api.SIPDispatchRuleIndividual(
                room_prefix="inbound_",
            )
        ),
        room_config=lk_api.RoomConfiguration(
            metadata=json.dumps({"type": "inbound"}),
            agents=[lk_api.RoomAgentDispatch(agent_name="my-agent")],
        ),
    )
    if trunk_id:
        request.trunk_ids.append(trunk_id)
    return request


async def _ensure_inbound_infrastructure(
    organization_id: str,
    hospital_name: str,
    vobiz_did: str,
) -> None:
    from livekit import api as lk_api

    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not api_key or not api_secret:
        logger.warning(
            "Guard skipped for %s: LiveKit credentials not set", hospital_name
        )
        return

    livekit_url = os.environ.get(
        "LIVEKIT_URL_INTERNAL",
        os.environ.get("LIVEKIT_URL", "http://livekit-server:7880"),
    )
    did_digits = vobiz_did.replace("+", "")
    trunk_name = f"Inbound_{did_digits}"
    rule_name = f"Route_{did_digits}"

    async with lk_api.LiveKitAPI(livekit_url, api_key, api_secret) as lk:
        inbound_trunks = await lk.sip.list_inbound_trunk(
            lk_api.ListSIPInboundTrunkRequest()
        )
        existing_trunk = next(
            (trunk for trunk in inbound_trunks.items if trunk.name == trunk_name), None
        )
        if existing_trunk:
            inbound_trunk_id = existing_trunk.sip_trunk_id
        else:
            logger.info(
                "Guard: creating inbound trunk for %s (%s)", hospital_name, vobiz_did
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

        rules = await lk.sip.list_dispatch_rule(lk_api.ListSIPDispatchRuleRequest())
        if not any(rule.name == rule_name for rule in rules.items):
            logger.info("Guard: creating dispatch rule for %s", vobiz_did)
            await lk.sip.create_dispatch_rule(
                lk_api.CreateSIPDispatchRuleRequest(
                    name=rule_name,
                    trunk_ids=[inbound_trunk_id],
                    rule=lk_api.SIPDispatchRule(
                        dispatch_rule_individual=lk_api.SIPDispatchRuleIndividual(
                            room_prefix=f"inbound_{did_digits}_",
                        )
                    ),
                    room_config=lk_api.RoomConfiguration(
                        metadata=json.dumps(
                            {
                                "organization_id": organization_id,
                                "hospital_name": hospital_name,
                                "type": "inbound",
                            }
                        ),
                        agents=[lk_api.RoomAgentDispatch(agent_name="my-agent")],
                    ),
                )
            )
        logger.info("Guard: infrastructure healthy for %s", hospital_name)


if __name__ == "__main__":
    asyncio.run(infrastructure_guard())
    cli.run_app(server)
