from __future__ import annotations

import subprocess
import uuid
from datetime import date
from typing import Any

from fastapi import Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.security import create_access_token, hash_password, verify_password
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.campaign import Campaign
from api.app.models.hospital.medivoice import MediVoiceSession, MediVoiceTranscription
from api.app.models.organization import HospitalConfig, Organization
from api.app.views.deps import get_current_user


def _parse_optional_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


async def files_insert(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.patients import upload_patients

    return await upload_patients(files, current_user, session)


async def fetch_patients(
    call_status: str = Query(default=""),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    department: str | None = Query(default=None),
    limit: str | None = Query(default=None),
    patient_ids: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.patients import list_patients

    return await list_patients(
        call_status,
        start_date,
        end_date,
        department,
        limit,
        patient_ids,
        current_user,
        session,
    )


async def files_log(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.patients import list_upload_logs

    return await list_upload_logs(current_user, session)


async def all_hospitals(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.tenant import list_hospitals

    return await list_hospitals(
        request=request, current_user=current_user, session=session
    )


async def patient_call_history(
    patient_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.patients import patient_call_history as view

    return await view(patient_id, current_user, session)


async def call_feedback(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import CallFeedbackIn
    from api.app.views.v1.hospital.feedback import post_call_feedback

    return await post_call_feedback(CallFeedbackIn(**body), current_user, session)


async def escalation_feedback(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import EscalationFeedbackIn
    from api.app.views.v1.hospital.feedback import post_escalation_feedback

    return await post_escalation_feedback(
        EscalationFeedbackIn(**body), current_user, session
    )


async def update_escalation(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import UpdateEscalationIn
    from api.app.views.v1.hospital.feedback import update_escalation as view

    return await view(UpdateEscalationIn(**body), current_user, session)


async def escalation_management(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.feedback import list_escalations

    return await list_escalations(current_user, session)


async def community_feedback(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import CommunityFeedbackIn
    from api.app.views.v1.hospital.feedback import post_community_feedback

    return await post_community_feedback(
        CommunityFeedbackIn(**body), current_user, session
    )


async def update_community(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import UpdateCommunityIn
    from api.app.views.v1.hospital.feedback import update_community as view

    return await view(UpdateCommunityIn(**body), current_user, session)


async def recent_activity(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.outbound_dashboard import recent_activity as view

    return await view(current_user, session)


async def admin_dashboard(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.outbound_dashboard import admin_dashboard as view

    return await view(current_user, session)


async def kpi_summary(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.outbound_dashboard import kpi_summary as view

    return await view(start_date, end_date, current_user, session)


async def patient_engagement(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.outbound_dashboard import patient_engagement as view

    return await view(start_date, end_date, current_user, session)


async def community_engagement(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.outbound_dashboard import (
        community_engagement as view,
    )

    return await view(start_date, end_date, current_user, session)


async def escalation_engagement(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.outbound_dashboard import (
        escalation_engagement as view,
    )

    return await view(start_date, end_date, current_user, session)


async def revisit_engagement(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.outbound_dashboard import revisit_analytics

    return await revisit_analytics(start_date, end_date, current_user, session)


# LEGACY: old Django endpoint was /api/system_logs/.
async def system_logs(
    service: str = Query(default="fettle-agent"),
    lines: int = Query(default=50),
    current_user: User = Depends(get_current_user),
) -> dict:
    if current_user.role not in {"superadmin", "hospital_admin"}:
        return {"error": 1, "errorMsg": "Unauthorized"}

    if service not in {"fettle-agent", "nginx_static", "web-prod"}:
        return {"error": 1, "errorMsg": "Invalid service"}

    lines = max(1, min(lines, 500))
    try:
        if service == "fettle-agent":
            cmd = ["journalctl", "-u", service, "--no-pager", "-n", str(lines)]
        else:
            container_name = (
                f"hospital_backend-{service}-1"
                if service != "nginx_static"
                else "nginx_static"
            )
            cmd = ["docker", "logs", container_name, "--tail", str(lines)]
        output = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, text=True, timeout=15
        )
        return {"error": 0, "logs": output}
    except Exception as exc:
        return {"error": 1, "errorMsg": str(exc)}


async def roi_metrics(
    call_direction: str = Query(default="outbound"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.outbound_dashboard import roi_metrics as view

    return await view(call_direction, start_date, end_date, current_user, session)


async def department_analytics(
    call_direction: str = Query(default="outbound"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.outbound_dashboard import (
        department_analytics as view,
    )

    return await view(call_direction, start_date, end_date, current_user, session)


# LEGACY: old Django endpoint was /api/campaigns/ with GET, POST, and DELETE.
async def campaigns(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    if request.method == "GET":
        from api.app.views.v1.hospital.management import list_campaigns

        return await list_campaigns(current_user, session)

    if request.method == "DELETE":
        campaign_id = request.query_params.get("id")
        if not campaign_id:
            return {"msg": "id is required", "error": 1}
        try:
            campaign_uuid = uuid.UUID(campaign_id)
        except ValueError:
            return {"msg": "Campaign not found", "error": 1}

        camp = await session.scalar(
            select(Campaign).where(
                Campaign.id == campaign_uuid,
                Campaign.organization_id == current_user.organization_id,
            )
        )
        if camp is None:
            return {"msg": "Campaign not found", "error": 1}
        await session.delete(camp)
        return {"msg": "Campaign deleted", "error": 0}

    body = await request.json()
    campaign = Campaign(
        id=uuid.uuid4(),
        organization_id=current_user.organization_id,
        name=body.get("name") or body.get("campaign_name") or "Default Campaign",
        purpose=body.get("purpose", ""),
        template_type=body.get("template_type", "custom"),
        audience_type=body.get("audience_type", "all"),
        patient_ids=body.get("patient_ids", []),
        start_date=_parse_optional_date(body.get("start_date")),
        end_date=_parse_optional_date(body.get("end_date")),
        unconnected_only=bool(body.get("unconnected_only", False)),
        status="created",
    )
    session.add(campaign)
    await session.flush()

    try:
        from api.app.worker.tasks import run_campaign_task

        run_campaign_task.delay(str(campaign.id))
    except Exception:
        pass

    return {
        "msg": "Campaign created and initiated",
        "id": str(campaign.id),
        "error": 0,
    }


async def tab_access(current_user: User = Depends(get_current_user)) -> dict:
    return {
        "error": 0,
        "permissions": {
            "patient_engagement": current_user.perm_patient_engagement,
            "community_engagement": current_user.perm_community_engagement,
            "revisit_engagement": current_user.perm_revisit_engagement,
            "escalation_engagement": current_user.perm_escalation_engagement,
            "calllog_engagement": current_user.perm_calllog_engagement,
            "upload_engagement": current_user.perm_upload_engagement,
            "pdf_engagement": current_user.perm_pdf_engagement,
        },
    }


# LEGACY: old Django endpoint was /api/doctor/login/.
async def doctor_login(
    body: dict = Body(...),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    email = str(body.get("email", "")).strip().lower()
    doctor = await session.scalar(
        select(User).where(
            User.email == email,
            User.role == "doctor",
            User.is_active.is_(True),
        )
    )
    if doctor is None or not verify_password(
        str(body.get("password", "")), doctor.password_hash
    ):
        return {"msg": "Invalid password", "error": 1}

    token = create_access_token(
        user_id=doctor.id,
        email=doctor.email,
        organization_id=doctor.organization_id,
        role=doctor.role,
    )
    hospital_name = (
        await session.scalar(
            select(Organization.name).where(Organization.id == doctor.organization_id)
        )
    ) or ""
    return {
        "msg": "Success",
        "token": token,
        "doctor_name": doctor.name,
        "hospital_name": hospital_name,
        "error": 0,
    }


# LEGACY: old Django endpoint accepted POST /api/doctor_management/.
async def create_or_update_doctor(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    if body.get("action") == "reset_password":
        doctor_id = body.get("id")
        try:
            doctor_uuid = uuid.UUID(str(doctor_id))
        except (TypeError, ValueError):
            return {"msg": "Doctor not found", "error": 1}
        doctor = await session.scalar(
            select(User).where(
                User.id == doctor_uuid,
                User.organization_id == current_user.organization_id,
                User.role == "doctor",
            )
        )
        if doctor is None:
            return {"msg": "Doctor not found", "error": 1}
        doctor.password_hash = hash_password("doctorpassword")
        return {"msg": "Password reset to default", "error": 0}

    from api.app.views.v1.hospital.management import DoctorCreateIn, create_doctor

    payload = dict(body)
    payload.setdefault("password", "doctorpassword")
    try:
        return await create_doctor(DoctorCreateIn(**payload), current_user, session)
    except HTTPException as exc:
        if exc.status_code == 400:
            return {"msg": "Doctor with this email already exists", "error": 1}
        raise


async def get_text(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    cfg = await session.scalar(
        select(HospitalConfig).where(
            HospitalConfig.organization_id == current_user.organization_id
        )
    )
    branding = dict(cfg.branding_json or {}) if cfg else {}
    return {"error": 0, "text": branding.get("text_template", "")}


async def save_text(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    cfg = await session.scalar(
        select(HospitalConfig).where(
            HospitalConfig.organization_id == current_user.organization_id
        )
    )
    if cfg is None:
        cfg = HospitalConfig(
            id=uuid.uuid4(),
            organization_id=current_user.organization_id,
            branding_json={},
        )
        session.add(cfg)
    branding = dict(cfg.branding_json or {})
    branding["text_template"] = body.get("text") or body.get("template") or ""
    cfg.branding_json = branding
    return {"error": 0, "msg": "Text template saved", "text": branding["text_template"]}


async def pdf_view(body: dict = Body(default_factory=dict)) -> Response:
    title = body.get("title") or "Fettle Document"
    lines = [str(title), ""]
    for key, value in body.items():
        if key != "title":
            lines.append(f"{key}: {value}")
    return Response(
        content="\n".join(lines).encode(),
        media_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="document.txt"'},
    )


async def outbound_call(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.calls import trigger_outbound

    return await trigger_outbound(body, current_user, session)


async def process_outbound_call(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    from api.app.views.v1.hospital.calls import process_outbound

    return await process_outbound(body, current_user)


async def download_excel(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    from api.app.views.v1.hospital.calls import export_outbound_excel

    return await export_outbound_excel(current_user, session)


async def show_inbound(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.calls import list_inbound_calls

    return await list_inbound_calls(start_date, end_date, current_user, session)


async def create_inbound(body: dict = Body(...)) -> dict:
    from api.app.worker.tasks import inbound_call_task

    task = inbound_call_task.delay(body)
    return {"error": 0, "task_id": task.id, "status": "queued"}


async def process_inbound_call(body: dict = Body(...)) -> dict:
    from api.app.worker.tasks import process_inbound_calls

    task = process_inbound_calls.delay(body)
    return {"error": 0, "task_id": task.id, "status": "queued"}


async def kpi_inbound(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.inbound_dashboard import kpi_inbound as view

    return await view(start_date, end_date, current_user, session)


async def patient_engagement_inbound(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.inbound_dashboard import (
        patient_engagement_inbound as view,
    )

    return await view(start_date, end_date, current_user, session)


async def community_engagement_inbound(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.inbound_dashboard import community_engagement_inbound

    return await community_engagement_inbound(
        start_date, end_date, current_user, session
    )


async def escalation_engagement_inbound(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.inbound_dashboard import (
        escalation_engagement_inbound,
    )

    return await escalation_engagement_inbound(
        start_date, end_date, current_user, session
    )


async def revisit_engagement_inbound(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.views.v1.hospital.inbound_dashboard import revisit_analytics_inbound

    return await revisit_analytics_inbound(start_date, end_date, current_user, session)


async def inbound_feedback(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import InboundCallFeedbackIn
    from api.app.views.v1.hospital.feedback import post_inbound_feedback

    return await post_inbound_feedback(
        InboundCallFeedbackIn(**body), current_user, session
    )


async def inbound_community_feedback(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import CommunityFeedbackIn
    from api.app.views.v1.hospital.feedback import post_inbound_community

    return await post_inbound_community(
        CommunityFeedbackIn(**body), current_user, session
    )


async def inbound_escalation_feedback(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import EscalationFeedbackIn
    from api.app.views.v1.hospital.feedback import post_inbound_escalation

    return await post_inbound_escalation(
        EscalationFeedbackIn(**body), current_user, session
    )


async def update_inbound_escalation(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Any:
    from api.app.schemas.hospital.feedback import UpdateEscalationIn
    from api.app.views.v1.hospital.feedback import update_inbound_escalation as view

    return await view(UpdateEscalationIn(**body), current_user, session)


async def vobiz_inbound_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.webhooks import vobiz_inbound_webhook as view

    return await view(request, session)


async def vobiz_outbound_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.webhooks import vobiz_outbound_webhook as view

    return await view(request, session)


async def vobiz_whatsapp_webhook(request: Request) -> dict:
    from api.app.views.v1.hospital.webhooks import vobiz_whatsapp_webhook as view

    return await view(request)


async def livekit_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from fastapi import BackgroundTasks

    from api.app.views.v1.hospital.webhooks import livekit_webhook as view

    return await view(request, BackgroundTasks(), session)


async def staff_availability(
    doctor_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.management import staff_availability as view

    return await view(doctor_id, current_user, session)


async def doctor_management(
    doctor_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.management import list_doctors

    return await list_doctors(doctor_id, current_user, session)


async def doctor_transcripts(
    doctor_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    target_doctor_id = doctor_id or current_user.id
    rows = (
        await session.execute(
            select(
                MediVoiceSession, func.count(MediVoiceTranscription.id).label("count")
            )
            .outerjoin(
                MediVoiceTranscription,
                MediVoiceTranscription.session_id == MediVoiceSession.id,
            )
            .where(
                MediVoiceSession.organization_id == current_user.organization_id,
                MediVoiceSession.doctor_id == target_doctor_id,
            )
            .group_by(MediVoiceSession.id)
            .order_by(MediVoiceSession.created_at.desc())
        )
    ).all()
    return {
        "error": 0,
        "data": [
            {
                "session_id": str(session_row.id),
                "patient_name": session_row.patient_name,
                "patient_mobile": session_row.patient_mobile,
                "diagnosis": session_row.diagnosis,
                "summary": session_row.overall_summary,
                "transcription_count": count,
                "created_at": session_row.created_at.isoformat(),
            }
            for session_row, count in rows
        ],
    }


async def medivoice_sessions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.medivoice import list_sessions

    return await list_sessions(current_user, session)


async def create_medivoice_session(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.medivoice import SessionCreateIn, create_session

    return await create_session(SessionCreateIn(**body), current_user, session)


async def medivoice_sync(
    request: Request,
    body: dict = Body(...),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    from api.app.views.v1.hospital.medivoice import SyncIn, sync_session

    return await sync_session(request, SyncIn(**body), session)


async def medivoice_prescription(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    from api.app.views.v1.hospital.medivoice import get_session_artifact

    return await get_session_artifact(session_id, "pdf", current_user, session)


async def transcription_artifacts(
    session_id: uuid.UUID,
    artifact_type: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    from api.app.views.v1.hospital.medivoice import get_session_artifact

    return await get_session_artifact(session_id, artifact_type, current_user, session)
