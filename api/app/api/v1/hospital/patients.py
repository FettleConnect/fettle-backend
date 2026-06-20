from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.call import InboundCall, OutboundCall
from api.app.models.hospital.patient import HospitalUploadLog, Patient
from api.app.models.organization import Organization
from api.app.schemas.hospital.patient import (
    CallHistoryItem,
    PatientCallHistoryResponse,
    PatientListResponse,
    PatientOut,
    UploadLogOut,
    UploadLogsResponse,
)
from api.app.services.s3 import get_presigned_url

router = APIRouter(prefix="/patients", tags=["patients"])

_STATUS_COLORS = {
    "connected": "#28A745",
    "not_connected": "#DC3545",
    "queued": "#FFC107",
    "in_progress": "#3B82F6",
}
_GRAY = "#6C757D"


@router.get("/", response_model=PatientListResponse)
async def list_patients(
    call_status: str = Query(
        default="", description="Comma-separated: connected,not_connected,in_progress"
    ),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    department: Optional[str] = Query(default=None),
    limit: Optional[str] = Query(
        default=None, description="Integer or 'all' or 'latest'"
    ),
    patient_ids: Optional[str] = Query(
        default=None, description="Comma-separated UUIDs"
    ),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PatientListResponse:
    org_id = current_user.organization_id

    # Build latest-call-status subquery using DISTINCT ON (PostgreSQL-specific, very efficient)
    latest_call = (
        select(
            OutboundCall.patient_id.label("patient_id"),
            OutboundCall.calling_process.label("calling_process"),
            OutboundCall.status.label("status"),
        )
        .where(OutboundCall.organization_id == org_id)
        .order_by(OutboundCall.patient_id, OutboundCall.created_at.desc())
        .distinct(OutboundCall.patient_id)
        .subquery()
    )

    org_name_col = (
        select(Organization.name).where(Organization.id == org_id).scalar_subquery()
    )

    calling_process_expr = func.coalesce(
        latest_call.c.calling_process, "not_connected"
    ).label("calling_process")

    q = (
        select(
            Patient.id,
            Patient.patient_name,
            Patient.mobile_no,
            Patient.department,
            Patient.age,
            org_name_col.label("hospital_name"),
            calling_process_expr,
            latest_call.c.status.label("raw_status"),
        )
        .outerjoin(latest_call, Patient.id == latest_call.c.patient_id)
        .where(Patient.organization_id == org_id)
        .order_by(Patient.uploaded_at.desc())
    )

    if start_date and end_date:
        q = q.where(func.date(Patient.uploaded_at).between(start_date, end_date))
    if department:
        q = q.where(Patient.department == department)

    # Handle "latest" limit — get patients uploaded within 5 min of the most recent upload
    effective_limit: int | None = None
    if limit and limit.strip().lower() == "latest":
        max_upload = await session.scalar(
            select(func.max(Patient.uploaded_at)).where(
                Patient.organization_id == org_id
            )
        )
        if max_upload:
            from datetime import timedelta

            q = q.where(Patient.uploaded_at >= max_upload - timedelta(minutes=5))
    elif limit and limit.strip().lower() not in ("", "all"):
        try:
            effective_limit = int(limit)
        except ValueError:
            pass

    if patient_ids:
        pid_list = [i.strip() for i in patient_ids.split(",") if i.strip()]
        try:
            pid_uuids = [uuid.UUID(p) for p in pid_list]
            q = q.where(Patient.id.in_(pid_uuids))
        except ValueError:
            pass

    if effective_limit:
        q = q.limit(effective_limit)

    rows = (await session.execute(q)).all()

    # Apply call_status filter in Python — only for pre-filtered set (post-SQL)
    status_filter = {s.strip().lower() for s in call_status.split(",") if s.strip()}

    data: list[PatientOut] = []
    for row in rows:
        raw = row.raw_status
        cp = row.calling_process
        # Normalise "queued" status from raw status column
        if cp == "not_happened":
            cp = "queued" if raw == "queued" else "not_connected"

        if status_filter and cp.lower() not in status_filter:
            continue

        data.append(
            PatientOut(
                id=row.id,
                patient_name=row.patient_name,
                mobile_no=row.mobile_no,
                department=row.department,
                age=row.age,
                hospital_name=row.hospital_name or "",
                whatsapp_link=f"https://web.whatsapp.com/send?phone={row.mobile_no}",
                calling_progress=cp,
                color=_STATUS_COLORS.get(cp, _GRAY),
            )
        )

    return PatientListResponse(data=data, count=len(data))


@router.get("/upload-logs", response_model=UploadLogsResponse)
async def list_upload_logs(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> UploadLogsResponse:
    rows = (
        (
            await session.execute(
                select(HospitalUploadLog)
                .where(
                    HospitalUploadLog.organization_id == current_user.organization_id
                )
                .order_by(HospitalUploadLog.uploaded_at.desc())
            )
        )
        .scalars()
        .all()
    )

    return UploadLogsResponse(
        data=[
            UploadLogOut(
                id=str(r.id),
                file_names=r.file_names,
                status=r.status,
                uploaded_at=r.uploaded_at,
                message=r.message,
            )
            for r in rows
        ]
    )


@router.get("/{patient_id}/history", response_model=PatientCallHistoryResponse)
async def patient_call_history(
    patient_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PatientCallHistoryResponse:
    patient = await session.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.organization_id == current_user.organization_id,
        )
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found"
        )

    out_rows = (
        (
            await session.execute(
                select(OutboundCall)
                .where(
                    OutboundCall.patient_id == patient_id,
                    OutboundCall.organization_id == current_user.organization_id,
                )
                .order_by(OutboundCall.started_at.desc())
            )
        )
        .scalars()
        .all()
    )

    last_10 = (
        patient.mobile_no[-10:] if len(patient.mobile_no) > 10 else patient.mobile_no
    )
    in_rows = (
        (
            await session.execute(
                select(InboundCall)
                .where(
                    InboundCall.organization_id == current_user.organization_id,
                    InboundCall.from_phone_number.like(f"%{last_10}"),
                )
                .order_by(InboundCall.started_at.desc())
            )
        )
        .scalars()
        .all()
    )

    history: list[CallHistoryItem] = []
    for c in out_rows:
        history.append(
            CallHistoryItem(
                id=str(c.id),
                type="outbound",
                started_at=c.started_at,
                status=c.calling_process,
                transcript_link=get_presigned_url(c.message_s3_link),
                audio_link=get_presigned_url(c.audio_s3_link),
                summary="AI Outbound Summary available in transcript link",
            )
        )
    for c in in_rows:
        history.append(
            CallHistoryItem(
                id=str(c.id),
                type="inbound",
                started_at=c.started_at,
                status=c.calling_process,
                transcript_link=get_presigned_url(c.message_s3_link),
                audio_link=get_presigned_url(c.audio_s3_link),
                summary="AI Inbound Summary available in transcript link",
            )
        )

    history.sort(
        key=lambda x: (x.started_at or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    return PatientCallHistoryResponse(history=history)


# ── Column mapping for CSV/XLSX upload (same as legacy) ──────────────────────
_COLUMN_MAP: dict[str, list[str]] = {
    "patient_name": ["Patient Name", "patient_name", "Name", "Name of the Patient"],
    "mobile_no": ["Mobile No", "mobile_no", "Mobile", "Phone", "Number", "mobile"],
    "department": ["Departments", "department", "Dept", "Specialization"],
    "age": ["Age", "age"],
    "serial_no": ["Sno.", "serial_no", "S.No", "ID"],
    "date": ["Date", "date", "Appointment Date"],
}


def _get_col(df_cols: list, key: str) -> str | None:
    clean = {str(c).strip().lower(): c for c in df_cols}
    for possible in _COLUMN_MAP.get(key, []):
        if possible.strip().lower() in clean:
            return clean[possible.strip().lower()]
    return None


@router.post("/upload")
async def upload_patients(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    org_id = current_user.organization_id
    filenames = [f.filename for f in files]

    log = HospitalUploadLog(
        id=uuid.uuid4(),
        organization_id=org_id,
        file_names=filenames,
        status="PENDING",
    )
    session.add(log)
    await session.flush()

    for file in files:
        if not file.filename or not (
            file.filename.endswith(".csv") or file.filename.endswith(".xlsx")
        ):
            log.status = "FAILED"
            log.message = f"Unsupported file type: {file.filename}"
            return {"msg": log.message, "error": 1}

    patient_ids_uploaded: list[str] = []

    for file in files:
        content = await file.read()
        try:
            if file.filename and file.filename.endswith(".xlsx"):
                df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
            else:
                df = pd.read_csv(io.BytesIO(content))
        except Exception as exc:
            log.status = "FAILED"
            log.message = f"Could not parse {file.filename}: {exc}"
            return {"msg": log.message, "error": 1}

        name_col = _get_col(list(df.columns), "patient_name")
        mobile_col = _get_col(list(df.columns), "mobile_no")

        if not name_col or not mobile_col:
            log.status = "FAILED"
            log.message = f"Missing Name/Mobile columns in {file.filename}"
            return {"msg": log.message, "error": 1}

        try:
            for _, row in df.iterrows():
                p_name = str(row[name_col])
                p_mobile = str(row[mobile_col]).strip()
                if p_mobile.endswith(".0"):
                    p_mobile = p_mobile[:-2]

                dept_col = _get_col(list(df.columns), "department")
                p_dept = str(row[dept_col]) if dept_col else "General"

                age_col = _get_col(list(df.columns), "age")
                p_age_raw = row.get(age_col) if age_col else None
                p_age = (
                    int(p_age_raw)
                    if p_age_raw is not None and pd.notnull(p_age_raw)
                    else None
                )

                sno_col = _get_col(list(df.columns), "serial_no")
                p_sno_raw = row.get(sno_col) if sno_col else None
                p_sno = (
                    int(p_sno_raw)
                    if p_sno_raw is not None and pd.notnull(p_sno_raw)
                    else None
                )

                date_col = _get_col(list(df.columns), "date")
                p_date = None
                if date_col:
                    raw_date = row.get(date_col)
                    if raw_date is not None and pd.notnull(raw_date):
                        try:
                            p_date = pd.to_datetime(raw_date).date()
                        except Exception:
                            p_date = None

                # Upsert patient using on_conflict_do_update
                existing = await session.scalar(
                    select(Patient).where(
                        Patient.organization_id == org_id,
                        Patient.mobile_no == p_mobile,
                        Patient.department == p_dept,
                        Patient.visit_date == p_date,
                    )
                )
                if existing:
                    existing.patient_name = p_name
                    existing.age = p_age
                    existing.serial_no = p_sno
                    patient_ids_uploaded.append(str(existing.id))
                else:
                    new_p = Patient(
                        id=uuid.uuid4(),
                        organization_id=org_id,
                        patient_name=p_name,
                        mobile_no=p_mobile,
                        department=p_dept,
                        age=p_age,
                        serial_no=p_sno,
                        visit_date=p_date,
                    )
                    session.add(new_p)
                    await session.flush()
                    patient_ids_uploaded.append(str(new_p.id))

        except Exception as exc:
            log.status = "FAILED"
            log.message = str(exc)
            return {"msg": str(exc), "error": 1}

    log.status = "SUCCESS"
    log.message = f"Successfully processed {len(patient_ids_uploaded)} records."
    return {"msg": "Upload successful", "error": 0, "patient_ids": patient_ids_uploaded}
