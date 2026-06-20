from __future__ import annotations

import base64
import os
import uuid

from fastapi import (APIRouter, Depends, HTTPException, Request)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.v1.chatbot import (_DOCTOR_DRAFT_SYSTEM,
                                    _chat_with_ai,
                                    _create_patient_token,
                                    _generate_doctor_draft,
                                    _get_current_doctor, _get_current_patient,
                                    _get_or_create_thread, _process_images)
from api.app.core.security import (TokenError, create_access_token,
                                   decode_token, verify_password)
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.dermatology import (DermatologyPatient,
                                                 DermatologyThread)
from api.app.models.organization import Organization
from api.app.models.shared import GlobalConfig

_GOOGLE_AUTH_ROUTER = APIRouter(tags=["auth-legacy"])
_LEGACY_ROUTER = APIRouter(prefix="/api", tags=["legacy-compat"])

_bearer = HTTPBearer(auto_error=False)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _decode_google_jwt(token: str) -> dict | None:
    """Best-effort decode of a Google/Supabase JWT without signature verification."""
    try:
        import jwt as pyjwt

        return pyjwt.decode(token, options={"verify_signature": False})
    except Exception:
        return None


async def _get_caller(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_async_session),
) -> dict | None:
    """Return decoded token payload for either a patient or a doctor/user token."""
    if not credentials:
        return None
    try:
        return decode_token(credentials.credentials)
    except TokenError:
        return None


# ── POST /auth/google/ ────────────────────────────────────────────────────────


class GoogleAuthIn(BaseModel):
    token: str | None = None
    email: str | None = None
    name: str | None = None
    is_magic_link: bool = False
    organization_id: str | None = None


@_GOOGLE_AUTH_ROUTER.post("/auth/google/")
async def google_auth(
    body: GoogleAuthIn,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Patient authentication via Google OAuth token or Supabase magic link.
    - Magic link path: `is_magic_link=true` + `email` provided by frontend
    - Google OAuth path: `token` is a Google credential JWT; decode to get email
    Returns a patient JWT (7-day expiry, role=patient).
    """
    email: str | None = body.email

    if not email and body.token:
        payload = _decode_google_jwt(body.token)
        if payload:
            email = payload.get("email")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    email = email.strip().lower()
    name = body.name or email.split("@")[0]

    # Resolve organization — use first active hospital if not provided
    org_id: uuid.UUID | None = None
    if body.organization_id:
        try:
            org_id = uuid.UUID(body.organization_id)
        except ValueError:
            pass
    if org_id is None:
        org = await session.scalar(
            select(Organization)
            .where(Organization.type == "hospital", Organization.is_active.is_(True))
            .order_by(Organization.created_at)
        )
        if org:
            org_id = org.id

    if org_id is None:
        raise HTTPException(status_code=400, detail="No active organization found")

    patient = await session.scalar(
        select(DermatologyPatient).where(
            DermatologyPatient.organization_id == org_id,
            DermatologyPatient.email == email,
        )
    )
    if patient is None:
        patient = DermatologyPatient(
            id=uuid.uuid4(), organization_id=org_id, email=email, name=name
        )
        session.add(patient)
        await session.flush()
    elif name and patient.name != name:
        patient.name = name

    token = _create_patient_token(patient, org_id)
    return {"token": token, "name": patient.name, "email": patient.email, "error": 0}


# ── GET /api/validate_token/ ──────────────────────────────────────────────────


@_LEGACY_ROUTER.get("/validate_token/")
async def validate_token_legacy(
    payload: dict | None = Depends(_get_caller),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    role = payload.get("role", "")
    user_id = payload.get("sub", "")

    if role == "patient":
        patient = await session.scalar(
            select(DermatologyPatient).where(
                DermatologyPatient.id == uuid.UUID(user_id)
            )
        )
        if not patient:
            raise HTTPException(status_code=401, detail="Patient not found")
        return {
            "error": 0,
            "name": patient.name,
            "email": patient.email,
            "role": "patient",
        }

    # Doctor / hospital user
    user = await session.scalar(
        select(User).where(User.id == uuid.UUID(user_id), User.is_active.is_(True))
    )
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    extra = {}
    if user.role == "hospital_user":
        org_name = await session.scalar(
            select(Organization.name).where(Organization.id == user.organization_id)
        )
        extra["hospital"] = org_name or ""

    return {
        "error": 0,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        **extra,
    }


# ── POST /api/login/ ──────────────────────────────────────────────────────────


class LegacyLoginIn(BaseModel):
    email: str
    password: str


@_LEGACY_ROUTER.post("/login/")
async def legacy_login(
    body: LegacyLoginIn,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Legacy login — handles both doctor and hospital user login in one endpoint.
    Returns `{ error: 0, token, msg }` format expected by the frontend.
    """
    email = body.email.strip().lower()
    user = await session.scalar(
        select(User).where(User.email == email, User.is_active.is_(True))
    )
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")


    token = create_access_token(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        role=user.role,
    )
    return {
        "error": 0,
        "token": token,
        "msg": "Login successful",
        "role": user.role,
        "name": user.name,
    }


# ── GET /api/doctor_tabs/ ─────────────────────────────────────────────────────


@_LEGACY_ROUTER.get("/doctor_tabs/")
async def doctor_tabs(
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Doctor dashboard: paid threads in tab format expected by the frontend."""
    threads = (
        await session.execute(
            select(DermatologyThread, DermatologyPatient)
            .join(
                DermatologyPatient,
                DermatologyThread.patient_id == DermatologyPatient.id,
            )
            .where(
                DermatologyThread.organization_id == doctor.organization_id,
                DermatologyThread.payment_status == "paid",
            )
            .order_by(DermatologyThread.updated_at.desc())
        )
    ).all()

    tabs = []
    for thread, patient in threads:
        intake = thread.intake_data or {}
        all_images: list[str] = []
        for url in intake.get("skin_images", []) + intake.get("report_images", []):
            if url not in all_images:
                all_images.append(url)
        for msg in thread.conversation or []:
            for url in msg.get("images") or []:
                if url not in all_images:
                    all_images.append(url)

        tabs.append(
            {
                "id": str(thread.id),
                "name": patient.name,
                "email": patient.email,
                "payment_status": thread.payment_status,
                "active": thread.status == "active",
                "intake_data": {
                    "duration": intake.get("duration", ""),
                    "symptoms": intake.get("symptoms", ""),
                    "location": intake.get("location", ""),
                    "medicationsTried": intake.get("meds", ""),
                    "relevantHealthHistory": intake.get("history", ""),
                    "images": all_images,
                },
                "doctor_draft": thread.draft_response or "",
                "is_read": thread.mode != "dermatologist_review",
                "updated_at": thread.updated_at.isoformat(),
                "mode": thread.mode,
                "messages": thread.conversation or [],
            }
        )

    return {"error": 0, "tabs_doc": tabs}


# ── POST /api/doctor_conversation/ ───────────────────────────────────────────


class DoctorConvIn(BaseModel):
    id: str  # thread ID


@_LEGACY_ROUTER.post("/doctor_conversation/")
async def doctor_conversation(
    body: DoctorConvIn,
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    thread = await session.scalar(
        select(DermatologyThread).where(
            DermatologyThread.id == uuid.UUID(body.id),
            DermatologyThread.organization_id == doctor.organization_id,
        )
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"error": 0, "conv": thread.conversation or []}


# ── GET / POST /api/system_prompt/ ───────────────────────────────────────────


@_LEGACY_ROUTER.get("/system_prompt/")
async def get_system_prompt(
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Return doctor's saved system prompt from GlobalConfig."""
    key = f"system_prompt_{doctor.id}"
    cfg = await session.scalar(select(GlobalConfig).where(GlobalConfig.key == key))
    prompt = cfg.value if cfg else ""
    return {"error": 0, "doctor_name": doctor.name, "prompt": prompt}


class SystemPromptIn(BaseModel):
    prompt: str
    doctor_name: str | None = None


@_LEGACY_ROUTER.post("/system_prompt/")
async def save_system_prompt(
    body: SystemPromptIn,
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Save doctor's custom system prompt in GlobalConfig."""
    key = f"system_prompt_{doctor.id}"
    existing = await session.scalar(select(GlobalConfig).where(GlobalConfig.key == key))
    if existing:
        existing.value = body.prompt
    else:
        session.add(GlobalConfig(key=key, value=body.prompt))

    if body.doctor_name and body.doctor_name != doctor.name:
        doctor.name = body.doctor_name

    return {"error": 0, "msg": "System prompt saved"}


# ── Patient chat legacy aliases ───────────────────────────────────────────────


@_LEGACY_ROUTER.get("/chat_history/")
async def legacy_chat_history(
    thread_id: str | None = None,
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    thread = None
    if thread_id:
        try:
            thread = await session.scalar(
                select(DermatologyThread).where(
                    DermatologyThread.id == uuid.UUID(thread_id),
                    DermatologyThread.patient_id == patient.id,
                )
            )
        except ValueError:
            pass
    if thread is None:
        thread = await _get_or_create_thread(session, patient)
    return {
        "error": 0,
        "conv": thread.conversation or [],
        "mode": thread.mode,
        "thread_id": str(thread.id),
    }


@_LEGACY_ROUTER.post("/chat_view/")
async def legacy_chat_view(
    request: Request,
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Delegates to the main chatbot chat handler."""
    from api.app.api.v1.chatbot import send_chat_message

    return await send_chat_message(request, patient, session)


@_LEGACY_ROUTER.get("/consultation_list/")
async def legacy_consultation_list(
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    threads = (
        (
            await session.execute(
                select(DermatologyThread)
                .where(DermatologyThread.patient_id == patient.id)
                .order_by(DermatologyThread.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    return {
        "error": 0,
        "history": [
            {
                "id": str(t.id),
                "name": t.name,
                "title": t.name,
                "mode": t.mode,
                "status": t.status,
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in threads
        ],
    }


@_LEGACY_ROUTER.post("/archive_consultation/")
async def legacy_archive(
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    await session.execute(
        update(DermatologyThread)
        .where(
            DermatologyThread.patient_id == patient.id,
            DermatologyThread.status == "active",
        )
        .values(status="completed")
    )
    new_thread = DermatologyThread(
        id=uuid.uuid4(),
        organization_id=patient.organization_id,
        patient_id=patient.id,
        name="New Consultation",
        mode="general_education",
        conversation=[],
        intake_data={},
    )
    session.add(new_thread)
    await session.flush()
    return {
        "error": 0,
        "thread_id": str(new_thread.id),
        "msg": "New consultation started",
    }


# ── Doctor chat + respond legacy aliases ─────────────────────────────────────


class LegacyDoctorChatIn(BaseModel):
    id: str
    question: str = ""
    systemPrompt: str | None = None
    contextData: str | None = None
    currentDraft: str | None = None


@_LEGACY_ROUTER.post("/doctor_chat_view/")
async def legacy_doctor_chat(
    body: LegacyDoctorChatIn,
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    thread = await session.scalar(
        select(DermatologyThread).where(
            DermatologyThread.id == uuid.UUID(body.id),
            DermatologyThread.organization_id == doctor.organization_id,
        )
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    if body.question == "REGENERATE_DRAFT":
        draft = _generate_doctor_draft(thread)
        if draft:
            thread.draft_response = draft
        return {
            "error": 0,
            "result": "Draft regenerated",
            "draft": thread.draft_response or "",
        }

    # Build context for the AI call
    system = body.systemPrompt or _DOCTOR_DRAFT_SYSTEM
    context_parts = []
    if body.contextData:
        context_parts.append(f"Context:\n{body.contextData}")
    if body.currentDraft:
        context_parts.append(f"Current Draft:\n{body.currentDraft}")

    question = body.question
    if context_parts:
        question = "\n\n".join(context_parts) + f"\n\nQuestion: {question}"

    ai_response = _chat_with_ai(system, thread.conversation or [], question)
    return {
        "result": ai_response,
        "response": ai_response,
        "message": ai_response,
        "role": "ai",
        "error": 0,
    }


@_LEGACY_ROUTER.post("/doctor_send_response/")
async def legacy_doctor_respond(
    request: Request,
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type:
        form = await request.form()
        conversation_id = form.get("id", "")
        message = form.get("question", "")
        images = form.getlist("image")
    else:
        data = await request.json()
        conversation_id = data.get("id", "")
        message = data.get("question", "")
        images = []

    thread = await session.scalar(
        select(DermatologyThread).where(
            DermatologyThread.id == uuid.UUID(conversation_id),
            DermatologyThread.organization_id == doctor.organization_id,
        )
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    conv = list(thread.conversation or [])
    doctor_msg: dict = {
        "id": f"doctor-{uuid.uuid4().hex}",
        "role": "doctor",
        "content": message,
        "senderName": doctor.name,
    }

    if images:
        urls = await _process_images(images, "", conv)
        if isinstance(urls, list) and urls:
            doctor_msg["images"] = urls

    conv.append(doctor_msg)
    thread.conversation = conv
    thread.mode = "final_output"
    return {"error": 0, "msg": "Response sent to patient"}


# ── PayPal payment endpoints ──────────────────────────────────────────────────

PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_API_BASE = os.environ.get("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")
CONSULTATION_PRICE_USD = float(os.environ.get("CONSULTATION_PRICE_USD", "15.00"))


async def _get_paypal_access_token() -> str:
    """Get a PayPal OAuth2 access token."""

    import httpx

    creds = base64.b64encode(
        f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{PAYPAL_API_BASE}/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


class PaymentIntentIn(BaseModel):
    amount: float | None = None
    thread_id: str | None = None


@_LEGACY_ROUTER.post("/create-payment-intent/")
async def create_payment_intent(
    body: PaymentIntentIn,
    patient: DermatologyPatient = Depends(_get_current_patient),
) -> dict:
    """Create a PayPal order for consultation payment."""
    amount = body.amount or CONSULTATION_PRICE_USD

    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        # Development fallback — return a mock order ID
        return {"orderID": f"DEV-ORDER-{uuid.uuid4().hex[:12].upper()}"}

    import httpx

    try:
        access_token = await _get_paypal_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{PAYPAL_API_BASE}/v2/checkout/orders",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "intent": "CAPTURE",
                    "purchase_units": [
                        {
                            "amount": {
                                "currency_code": "USD",
                                "value": f"{amount:.2f}",
                            },
                            "description": "Fettle Dermatology Consultation",
                        }
                    ],
                },
            )
            resp.raise_for_status()
            return {"orderID": resp.json()["id"]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PayPal error: {exc}") from exc


class CapturePaymentIn(BaseModel):
    orderID: str
    thread_id: str | None = None


@_LEGACY_ROUTER.post("/capture_payment/")
async def capture_payment(
    body: CapturePaymentIn,
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Capture a PayPal order and mark the thread as paid."""
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        # Dev fallback — mark thread as paid without real PayPal
        if body.thread_id:
            try:
                await session.execute(
                    update(DermatologyThread)
                    .where(
                        DermatologyThread.id == uuid.UUID(body.thread_id),
                        DermatologyThread.patient_id == patient.id,
                    )
                    .values(payment_status="paid", mode="post_payment_intake")
                )
            except Exception:
                pass
        return {"status": "COMPLETED", "orderID": body.orderID}

    import httpx

    try:
        access_token = await _get_paypal_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{PAYPAL_API_BASE}/v2/checkout/orders/{body.orderID}/capture",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") == "COMPLETED":
            # Mark the patient's active thread as paid
            thread = await _get_or_create_thread(session, patient)
            thread.payment_status = "paid"
            thread.mode = "post_payment_intake"

        return {"status": data.get("status", "UNKNOWN"), "orderID": body.orderID}
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"PayPal capture error: {exc}"
        ) from exc
