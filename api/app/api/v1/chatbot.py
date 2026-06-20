from __future__ import annotations

import base64
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import (APIRouter, Depends, HTTPException, Request,
                     status)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.security import TokenError, create_access_token, decode_token
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.dermatology import (DermatologyPatient,
                                                 DermatologyThread)
from api.app.models.organization import Organization
from api.app.services.llm import llm_client, openrouter_model

router = APIRouter(prefix="/chatbot", tags=["chatbot"])

_bearer = HTTPBearer()

# ── Patient JWT helpers ───────────────────────────────────────────────────────


def _create_patient_token(patient: DermatologyPatient, org_id: uuid.UUID) -> str:
    """Create a long-lived JWT (7 days) for a dermatology patient."""

    return create_access_token(
        user_id=patient.id,
        email=patient.email,
        organization_id=org_id,
        role="patient",
        expires_delta=timedelta(days=7),
    )


async def _get_current_patient(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_async_session),
) -> DermatologyPatient:
    try:
        payload = decode_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("role") != "patient":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Patient token required"
        )

    patient = await session.scalar(
        select(DermatologyPatient).where(
            DermatologyPatient.id == uuid.UUID(payload["sub"])
        )
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Patient not found"
        )
    return patient


async def _get_current_doctor(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    try:
        payload = decode_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await session.scalar(
        select(User).where(
            User.id == uuid.UUID(payload["sub"]), User.is_active.is_(True)
        )
    )
    if user is None or user.role != "doctor":
        raise HTTPException(status_code=403, detail="Doctor token required")
    return user


# ── AI helpers ────────────────────────────────────────────────────────────────

_GENERAL_EDUCATION_SYSTEM = """You are a helpful dermatology education assistant. Your role is to:
1. Provide general information about skin conditions, treatments, and skincare
2. Help patients understand symptoms and when to seek professional medical care
3. Answer educational questions about dermatology in simple, clear language
4. NEVER diagnose specific conditions or prescribe treatments
5. Always recommend consulting a qualified dermatologist for personal medical advice

Be empathetic, informative, and supportive. Keep responses concise and clear."""

_DOCTOR_DRAFT_SYSTEM = """You are assisting a dermatologist in reviewing a patient's intake.
Generate a structured clinical draft response based on the patient's intake information.
Format:
**Clinical Assessment:**
[Brief assessment based on reported symptoms]

**Recommended Tests/Examination:**
[Specific tests or examinations to consider]

**Provisional Diagnosis:**
[Possible conditions to investigate]

**Treatment Considerations:**
[Potential treatment approaches]

**Patient Instructions:**
[Clear instructions for the patient]

Be clinical, precise, and base your draft strictly on the provided information."""


def _chat_with_ai(system: str, history: list[dict], question: str) -> str:
    """Call OpenRouter LLM with conversation history. Returns AI response text."""
    messages = [{"role": "system", "content": system}]
    for msg in history[-10:]:  # Keep last 10 messages for context
        role = (
            "assistant"
            if msg.get("role", "").lower() in ("ai", "assistant")
            else "user"
        )
        content = msg.get("content", "")
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=openrouter_model(),
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        return (
            f"I'm sorry, I was unable to generate a response. Please try again. ({exc})"
        )


def _generate_doctor_draft(thread: DermatologyThread) -> str:
    """Generate AI draft for doctor review based on intake data."""
    intake = thread.intake_data or {}
    conv = thread.conversation or []

    user_content = (
        f"Patient Intake Summary:\n"
        f"- Duration: {intake.get('duration', 'Not provided')}\n"
        f"- Symptoms: {intake.get('symptoms', 'Not provided')}\n"
        f"- Location on body: {intake.get('location', 'Not provided')}\n"
        f"- Medications Tried: {intake.get('meds', 'Not provided')}\n"
        f"- Prior History: {intake.get('history', 'Not provided')}\n"
        f"- Skin Images: {len(intake.get('skin_images', []))} uploaded\n"
        f"- Report Images: {len(intake.get('report_images', []))} uploaded\n\n"
        "Generate a clinical draft strictly following the format."
    )

    try:
        return _chat_with_ai(_DOCTOR_DRAFT_SYSTEM, conv, user_content)
    except Exception:
        return ""


def _check_image_for_face_ai(image_bytes: bytes) -> bool:
    """Returns True if a human face is detected in the image."""
    try:
        b64 = base64.b64encode(image_bytes).decode()
        client = llm_client()
        resp = client.chat.completions.create(
            model=openrouter_model(),
            max_tokens=10,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "low",
                            },
                        },
                        {
                            "type": "text",
                            "text": "Does this image contain a human face with clearly visible eyes, nose, and mouth together? Reply FACE_DETECTED or NO_FACE only.",
                        },
                    ],
                }
            ],
        )
        return "FACE_DETECTED" in resp.choices[0].message.content.strip().upper()
    except Exception:
        return False


def _check_image_for_pii_ai(image_bytes: bytes) -> bool:
    """Returns True if PII (name, DOB, phone, ID) is visible in the image."""
    try:
        b64 = base64.b64encode(image_bytes).decode()
        client = llm_client()
        resp = client.chat.completions.create(
            model=openrouter_model(),
            max_tokens=10,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "low",
                            },
                        },
                        {
                            "type": "text",
                            "text": "Does this image contain clearly readable personal information such as a patient name, date of birth, phone number, or ID number? Reply PII_DETECTED or PII_CLEAR only.",
                        },
                    ],
                }
            ],
        )
        return "PII_DETECTED" in resp.choices[0].message.content.strip().upper()
    except Exception:
        return False


async def _upload_image_to_s3(image_bytes: bytes, filename: str) -> str:
    """Upload image to S3 and return the presigned URL."""
    from api.app.services.s3 import get_presigned_url, upload_bytes_to_s3

    key = f"dermatology_images/{uuid.uuid4().hex}_{filename}"
    s3_url = await upload_bytes_to_s3(image_bytes, key, "image/jpeg")
    return get_presigned_url(s3_url) or s3_url


# ── Thread helpers ────────────────────────────────────────────────────────────


async def _get_or_create_thread(
    session: AsyncSession, patient: DermatologyPatient
) -> DermatologyThread:
    thread = await session.scalar(
        select(DermatologyThread)
        .where(
            DermatologyThread.patient_id == patient.id,
            DermatologyThread.status == "active",
        )
        .order_by(DermatologyThread.created_at.desc())
    )
    if thread is None:
        thread = DermatologyThread(
            id=uuid.uuid4(),
            organization_id=patient.organization_id,
            patient_id=patient.id,
            name="Consultation",
            mode="general_education",
            conversation=[],
            intake_data={},
        )
        session.add(thread)
        await session.flush()
    return thread


# ── Auth ──────────────────────────────────────────────────────────────────────


class PatientAuthIn(BaseModel):
    email: str
    name: str = ""
    organization_id: uuid.UUID


@router.post("/auth")
async def patient_auth(
    body: PatientAuthIn,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Authenticate a dermatology patient by email. Creates the patient if new."""
    if not body.email:
        raise HTTPException(status_code=422, detail="Email is required")

    org = await session.scalar(
        select(Organization).where(Organization.id == body.organization_id)
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    email = body.email.strip().lower()
    patient = await session.scalar(
        select(DermatologyPatient).where(
            DermatologyPatient.organization_id == body.organization_id,
            DermatologyPatient.email == email,
        )
    )

    if patient is None:
        name = body.name or email.split("@")[0]
        patient = DermatologyPatient(
            id=uuid.uuid4(),
            organization_id=body.organization_id,
            email=email,
            name=name,
        )
        session.add(patient)
        await session.flush()
    elif body.name and patient.name != body.name:
        patient.name = body.name

    token = _create_patient_token(patient, body.organization_id)
    return {"error": 0, "token": token, "name": patient.name, "email": patient.email}


# ── Patient chat ──────────────────────────────────────────────────────────────


@router.get("/chat")
async def get_chat_history(
    thread_id: Optional[str] = None,
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    if thread_id:
        try:
            thread = await session.scalar(
                select(DermatologyThread).where(
                    DermatologyThread.id == uuid.UUID(thread_id),
                    DermatologyThread.patient_id == patient.id,
                )
            )
        except ValueError:
            thread = None
        if thread is None:
            thread = await _get_or_create_thread(session, patient)
    else:
        thread = await _get_or_create_thread(session, patient)

    return {
        "error": 0,
        "conv": thread.conversation or [],
        "mode": thread.mode,
        "thread_id": str(thread.id),
        "payment_status": thread.payment_status,
        "draft_response": thread.draft_response,
    }


@router.post("/chat")
async def send_chat_message(
    request: Request,
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Send a patient message. Handles:
    - general_education: AI responds via LLM
    - post_payment_intake: stores intake data, no AI response
    - dermatologist_review / final_output: AI silent (FIX 1 guard)
    - question == "CONFIRM": transitions to post_payment_intake
    - Image uploads: face/PII detection, S3 upload
    """
    # Parse multipart OR JSON
    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type:
        form = await request.form()
        question = form.get("question", "")
        step = form.get("step", "")
        thread_id = form.get("thread_id", "")
        images = form.getlist("image")  # type: ignore[attr-defined]
    else:
        data = await request.json()
        question = data.get("question", "")
        step = data.get("step", "")
        thread_id = data.get("thread_id", "")
        images = []

    # Resolve thread
    thread: DermatologyThread | None = None
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

    conv = list(thread.conversation or [])

    # ── FIX 1: Guard — silent modes ──────────────────────────────────────────
    if thread.mode in ("dermatologist_review", "final_output"):
        if question and question not in ("", "CONFIRM"):
            user_msg: dict = {
                "id": f"user-{uuid.uuid4().hex}",
                "role": "user",
                "content": question,
            }
            if images:
                urls = await _process_images(images, step, conv)
                if isinstance(urls, str):  # error string from detection
                    return {
                        "result": urls,
                        "response": urls,
                        "role": "ai",
                        "mode": thread.mode,
                    }
                if urls:
                    user_msg["images"] = urls
            conv.append(user_msg)
            thread.conversation = conv
        return {
            "result": "",
            "response": "",
            "message": "",
            "role": "ai",
            "mode": thread.mode,
        }

    # ── Payment confirmation ─────────────────────────────────────────────────
    if question == "CONFIRM":
        thread.payment_status = "paid"
        thread.mode = "post_payment_intake"
        return {"result": "", "role": "ai", "mode": thread.mode}

    # ── Image upload (face/PII detection + S3) ───────────────────────────────
    new_img_urls: list[str] = []
    if images:
        result = await _process_images(images, step, conv)
        if isinstance(result, str):  # detection error
            return {
                "result": result,
                "response": result,
                "role": "ai",
                "mode": thread.mode,
            }
        new_img_urls = result

    # ── Build user message ───────────────────────────────────────────────────
    user_msg = {"id": f"user-{uuid.uuid4().hex}", "role": "user", "content": question}
    if new_img_urls:
        user_msg["images"] = new_img_urls
    conv.append(user_msg)

    # ── post_payment_intake: store intake, no AI response ────────────────────
    if thread.mode == "post_payment_intake":
        intake = dict(thread.intake_data or {})

        if step == "skin_image" and new_img_urls:
            skin = intake.get("skin_images", [])
            skin.extend(u for u in new_img_urls if u not in skin)
            intake["skin_images"] = skin
        elif step == "report_image" and new_img_urls:
            reports = intake.get("report_images", [])
            reports.extend(u for u in new_img_urls if u not in reports)
            intake["report_images"] = reports

        # Detect intake completion signal
        if "INTAKE COMPLETE" in question and "Summary:" in question:
            for line in question.splitlines():
                line = line.strip()
                for key, prefix in [
                    ("duration", "Duration:"),
                    ("symptoms", "Symptoms:"),
                    ("location", "Location:"),
                    ("meds", "Meds:"),
                    ("history", "History:"),
                ]:
                    if line.startswith(prefix):
                        intake[key] = line[len(prefix) :].strip()

            thread.mode = "dermatologist_review"
            thread.intake_data = intake
            thread.conversation = conv

            # Background draft generation — non-blocking

            draft = _generate_doctor_draft(thread)
            if draft:
                thread.draft_response = draft

            return {"result": "", "role": "ai", "mode": "dermatologist_review"}

        thread.intake_data = intake
        thread.conversation = conv
        return {"result": "", "role": "ai", "mode": thread.mode}

    # ── general_education: AI responds ───────────────────────────────────────
    ai_response = _chat_with_ai(_GENERAL_EDUCATION_SYSTEM, conv[:-1], question)

    ai_msg = {"id": f"ai-{uuid.uuid4().hex}", "role": "AI", "content": ai_response}
    conv.append(ai_msg)
    thread.conversation = conv

    return {
        "result": ai_response,
        "response": ai_response,
        "message": ai_response,
        "role": "ai",
        "mode": thread.mode,
    }


async def _process_images(images, step: str, existing_conv: list) -> list[str] | str:
    """
    Upload images to S3 with optional face/PII detection.
    Returns list of URLs, or an error string if detection blocks the image.
    """
    existing_urls = {u for m in existing_conv for u in (m.get("images") or [])}
    new_urls: list[str] = []

    for img in images:
        if hasattr(img, "read"):
            data = await img.read() if hasattr(img.read, "__await__") else img.read()  # type: ignore[union-attr]
            filename = getattr(img, "filename", "image.jpg") or "image.jpg"
        else:
            data = img
            filename = "image.jpg"

        if step == "skin_image":
            if _check_image_for_face_ai(data):
                return "face detected — no clinical image of skin issue is visible"
        elif step == "report_image":
            if _check_image_for_pii_ai(data):
                return "personal information visible — please redact name or number and re-upload"

        try:
            url = await _upload_image_to_s3(data, filename)
        except Exception:
            # If S3 not configured, use a placeholder
            url = f"data:image/jpeg;base64,{base64.b64encode(data[:100]).decode()}..."

        if url not in existing_urls:
            new_urls.append(url)
            existing_urls.add(url)

    return new_urls


# ── Patient consultation management ──────────────────────────────────────────


@router.get("/consultations")
async def list_consultations(
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
                "mode": t.mode,
                "status": t.status,
                "payment_status": t.payment_status,
                "created_at": t.created_at.isoformat(),
            }
            for t in threads
        ],
    }


@router.post("/archive")
async def archive_consultation(
    patient: DermatologyPatient = Depends(_get_current_patient),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    # Complete all active threads
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


# ── Doctor views ──────────────────────────────────────────────────────────────


@router.get("/doctor/consultations")
async def doctor_list_consultations(
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Return all paid threads for the doctor's organization."""
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
            .order_by(DermatologyThread.created_at.desc())
        )
    ).all()

    conversations = []
    for thread, patient in threads:
        intake = thread.intake_data or {}
        all_images = []
        for url in intake.get("skin_images", []) + intake.get("report_images", []):
            if url not in all_images:
                all_images.append(url)
        for msg in thread.conversation or []:
            for url in msg.get("images") or []:
                if url not in all_images:
                    all_images.append(url)

        conversations.append(
            {
                "id": str(thread.id),
                "patient_id": str(patient.id),
                "patientEmail": patient.email,
                "patientName": patient.name,
                "mode": thread.mode,
                "paymentStatus": thread.payment_status,
                "status": thread.status,
                "draftResponse": thread.draft_response or "",
                "intakeData": {
                    "duration": intake.get("duration", ""),
                    "symptoms": intake.get("symptoms", ""),
                    "location": intake.get("location", ""),
                    "medicationsTried": intake.get("meds", ""),
                    "relevantHealthHistory": intake.get("history", ""),
                    "images": all_images,
                },
                "messages": thread.conversation or [],
            }
        )

    return {"error": 0, "conversations": conversations}


class DoctorChatIn(BaseModel):
    id: str  # thread ID
    question: str = ""


@router.post("/doctor/chat")
async def doctor_ai_chat(
    body: DoctorChatIn,
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Doctor AI consultation or draft regeneration."""
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
            "result": "Draft regenerated",
            "draft": thread.draft_response or "",
            "error": 0,
        }

    conv = thread.conversation or []
    ai_response = _chat_with_ai(_DOCTOR_DRAFT_SYSTEM, conv, body.question)

    return {
        "result": ai_response,
        "response": ai_response,
        "message": ai_response,
        "role": "ai",
        "error": 0,
    }


class DoctorRespondIn(BaseModel):
    id: str  # thread ID
    question: str = ""  # doctor's response message


@router.post("/doctor/respond")
async def doctor_send_response(
    body: DoctorRespondIn,
    doctor: User = Depends(_get_current_doctor),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Doctor sends final response to patient — transitions thread to final_output."""
    thread = await session.scalar(
        select(DermatologyThread).where(
            DermatologyThread.id == uuid.UUID(body.id),
            DermatologyThread.organization_id == doctor.organization_id,
        )
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    conv = list(thread.conversation or [])
    conv.append(
        {
            "id": f"doctor-{uuid.uuid4().hex}",
            "role": "doctor",
            "content": body.question,
            "senderName": doctor.name,
        }
    )

    thread.conversation = conv
    thread.mode = "final_output"

    return {"error": 0, "msg": "Response sent to patient"}
