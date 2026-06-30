from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.security import hash_password
from api.app.models.auth import User
from api.app.models.hospital.transcribe import Job, KpiEvent, StoredFile
from api.app.models.organization import Organization
from api.app.transcribe.domain.models import (
    TranscribeArtifact,
    TranscribeDraftDocument,
    TranscribeIntakeLink,
    TranscribePdfDeliveryJob,
    TranscribePdfDeliverySettings,
    TranscribeQueueEntry,
    TranscribeReportContextDocument,
    TranscribeReportContextItem,
    TranscribeSession,
    TranscribeSessionAudio,
    TranscribeSyncJob,
    TranscribeTranscription,
)
from api.app.transcribe.id_codec import decode_public_id, encode_public_id
from api.app.transcribe.preloaded_doctors import PRELOADED_DOCTORS
from api.app.transcribe.schemas.common import (
    ConsultationSession,
    DoctorSummary,
    DoctorProfile,
    DocumentArtifact,
    DocumentArtifactDelivery,
    PatientSummary,
    PublicDoctorProfile,
    ReportContextChunk,
    ReportContextProgress,
    SessionAudioArtifact,
    SessionReportContext,
    SessionTranscription,
    SessionTranscriptionDraftDocument,
    TranscriptSegment,
)
from api.app.transcribe.schemas.queue import (
    IntakeLink,
    PatientProfile,
    QueueEntry,
    QueuePatient,
)

PDF_DELIVERY_BLOCK_TTL_SECONDS = 120
REPORT_CONTEXT_UPLOAD_TTL = timedelta(hours=24)
TRANSCRIBE_ORG_SLUG = "transcribe"


def _encode_doctor_id(value: uuid.UUID) -> str:
    return str(value)


def _decode_doctor_id(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _decode_session_id(value: str) -> int:
    return decode_public_id(value, "session")


def _decode_report_job_id(value: str) -> int:
    return decode_public_id(value, "report_job")


def _decode_report_upload_id(value: str) -> int:
    return decode_public_id(value, "report_upload")


def _decode_queue_entry_id(value: str) -> int:
    return decode_public_id(value, "queue_entry")


def _decode_intake_link_id(value: str) -> int:
    return decode_public_id(value, "intake_link")


def _decode_pdf_delivery_job_id(value: str) -> int:
    return decode_public_id(value, "pdf_delivery_job")


def _decode_sync_job_id(value: str) -> int:
    return decode_public_id(value, "sync_job")


def _slugify(value: str) -> str:
    normalized = value.strip().lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return re.sub(r"-{2,}", "-", normalized).strip("-")


def _serialize_datetime(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _serialize_datetime_string(value: datetime) -> str:
    return _serialize_datetime(value).isoformat().replace("+00:00", "Z")


def _coerce_iso_datetime_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _serialize_datetime_string(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return _serialize_datetime_string(
                datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            )
        except ValueError:
            return candidate
    return None


def _doctor_summary_from_model(model: User) -> DoctorSummary:
    return DoctorSummary(
        name=model.name,
        department=model.department,
        **({"phoneNumber": model.mobile_number} if model.mobile_number else {}),
        **({"email": model.email} if model.email else {}),
    )


def _doctor_profile_from_model(model: User) -> DoctorProfile:
    return DoctorProfile(
        id=_encode_doctor_id(model.id),
        name=model.name,
        department=model.department,
        **({"phoneNumber": model.mobile_number} if model.mobile_number else {}),
        **({"email": model.email} if model.email else {}),
        createdAt=_serialize_datetime(model.created_at),
        lastLoginAt=_serialize_datetime(model.last_login_at or model.created_at),
    )


def _public_doctor_profile_from_model(model: User) -> PublicDoctorProfile:
    return PublicDoctorProfile(
        id=_encode_doctor_id(model.id),
        name=model.name,
        department=model.department,
    )


def _intake_link_from_model(
    model: TranscribeIntakeLink, doctor_name: str
) -> IntakeLink:
    return IntakeLink(
        id=encode_public_id("intake_link", model.id),
        doctorId=_encode_doctor_id(model.doctor_id),
        doctorName=doctor_name,
        label=model.label,
        token=model.token,
        isActive=model.is_active,
        createdAt=_serialize_datetime(model.created_at),
        updatedAt=_serialize_datetime(model.updated_at),
    )


def _queue_entry_from_model(model: TranscribeQueueEntry) -> QueueEntry:
    return QueueEntry(
        id=encode_public_id("queue_entry", model.id),
        doctorId=_encode_doctor_id(model.doctor_id),
        intakeLinkId=(
            encode_public_id("intake_link", model.intake_link_id)
            if model.intake_link_id is not None
            else None
        ),
        intakeLabel=model.intake_label,
        patient=QueuePatient(
            name=model.patient_name,
            phoneNumber=model.patient_phone_number or "",
            age=model.patient_age,
        ),
        status=model.status,  # type: ignore[arg-type]
        source=model.source,  # type: ignore[arg-type]
        submittedAt=_serialize_datetime(model.submitted_at),
        acceptedAt=(
            _serialize_datetime(model.accepted_at) if model.accepted_at else None
        ),
        sessionId=(
            encode_public_id("session", model.session_id)
            if model.session_id is not None
            else None
        ),
        createdAt=_serialize_datetime(model.created_at),
        updatedAt=_serialize_datetime(model.updated_at),
    )


def _pdf_delivery_settings_payload(model: TranscribePdfDeliverySettings | None) -> dict:
    updated_at = model.updated_at if model is not None else datetime.now(UTC)
    pharmacy = None
    if model is not None and model.pharmacy_name and model.pharmacy_phone_number:
        pharmacy = {
            "name": model.pharmacy_name,
            "phoneNumber": model.pharmacy_phone_number,
        }
    return {
        "patientEnabled": model.patient_enabled if model is not None else True,
        "pharmacyEnabled": model.pharmacy_enabled if model is not None else False,
        **({"pharmacy": pharmacy} if pharmacy is not None else {}),
        "updatedAt": updated_at.isoformat(),
    }


def _pdf_delivery_job_payload(model: TranscribePdfDeliveryJob) -> dict:
    return {
        "id": encode_public_id("pdf_delivery_job", model.id),
        "sessionId": encode_public_id("session", model.session_id),
        "recipientType": model.recipient_type,
        "recipientName": model.recipient_name,
        "phoneNumber": model.phone_number,
        "status": model.status,
        "attemptCount": model.attempt_count,
        **({"provider": model.provider} if model.provider else {}),
        **(
            {"providerMessageId": model.provider_message_id}
            if model.provider_message_id
            else {}
        ),
        **({"lastError": model.last_error} if model.last_error else {}),
        "createdAt": model.created_at.isoformat(),
        "updatedAt": model.updated_at.isoformat(),
        **({"sentAt": model.sent_at.isoformat()} if model.sent_at else {}),
    }


def _report_context_chunk_payload(
    item: TranscribeReportContextItem,
    stored_file: StoredFile | None,
    created_at: datetime,
) -> ReportContextChunk:
    source_file_name = item.source_file_name or (
        stored_file.original_filename if stored_file is not None else "Imported report"
    )
    title = item.label or source_file_name
    return ReportContextChunk(
        id=encode_public_id("report_chunk", item.id),
        sourceFileName=source_file_name,
        sourceIndex=item.source_index,
        pageNumber=item.page_number,
        title=title,
        category=item.category or "report_context",
        text=item.extracted_text,
        extractedAt=_serialize_datetime(item.extracted_at or created_at),
        label=title,
    )


def _parse_report_context_state(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_report_context_state(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = payload or {}
    progress = normalized.get("progress")
    normalized_progress = None
    if isinstance(progress, dict):
        normalized_progress = {
            str(key): value.isoformat() if isinstance(value, datetime) else value
            for key, value in progress.items()
        }
    updated_at = normalized.get("updatedAt")
    return {
        "status": str(normalized.get("status") or "ready"),
        "selectedChunkIds": [
            str(chunk_id)
            for chunk_id in normalized.get("selectedChunkIds", [])
            if str(chunk_id).strip()
        ],
        "appliedChunkIds": [
            str(chunk_id)
            for chunk_id in normalized.get("appliedChunkIds", [])
            if str(chunk_id).strip()
        ],
        "extractedUploadIds": [
            str(upload_id)
            for upload_id in normalized.get("extractedUploadIds", [])
            if str(upload_id).strip()
        ],
        "error": (
            str(normalized.get("error")).strip()
            if str(normalized.get("error") or "").strip()
            else None
        ),
        "updatedAt": (
            updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at
        ),
        "progress": normalized_progress,
    }


def _sync_job_payload(model: TranscribeSyncJob) -> dict:
    return {
        "id": encode_public_id("sync_job", model.id),
        "sessionId": model.external_session_id
        or (
            encode_public_id("session", model.session_id)
            if model.session_id is not None
            else "dry-run"
        ),
        "trigger": model.trigger,
        "status": model.status,
        "attempts": model.attempts,
        "maxAttempts": model.max_attempts,
        "firstQueuedAt": model.first_queued_at.isoformat(),
        "nextAttemptAt": model.next_attempt_at.isoformat(),
        "updatedAt": model.updated_at.isoformat(),
        **({"lastError": model.last_error} if model.last_error else {}),
        **(
            {"lastErrorClass": model.last_error_class} if model.last_error_class else {}
        ),
        **({"finishedAt": model.finished_at.isoformat()} if model.finished_at else {}),
    }


def _public_job_status(status: str) -> str:
    return {
        "queued": "queued",
        "processing": "processing",
        "completed": "ready",
        "failed": "failed",
        "cancelled": "canceled",
        "canceled": "canceled",
    }.get(status, status)


async def _session_from_model(
    session: AsyncSession, model: TranscribeSession
) -> ConsultationSession:
    doctor_model = await session.get(User, model.doctor_id)
    if doctor_model is None:
        raise ValueError(f"doctor {model.doctor_id} not found for session {model.id}")
    audio_stmt = (
        select(TranscribeSessionAudio, StoredFile)
        .join(StoredFile, StoredFile.id == TranscribeSessionAudio.stored_file_id)
        .where(TranscribeSessionAudio.session_id == model.id)
        .order_by(TranscribeSessionAudio.created_at.desc())
        .limit(1)
    )
    audio_row = (await session.execute(audio_stmt)).first()
    audio_artifact = None
    if audio_row is not None:
        audio_model, file_model = audio_row
        audio_artifact = SessionAudioArtifact(
            id=encode_public_id("audio_artifact", audio_model.id),
            fileName=file_model.original_filename,
            mimeType=file_model.content_type,
            sizeBytes=file_model.size_bytes,
            durationSeconds=float(audio_model.duration_seconds or 0),
            uploadedAt=_serialize_datetime(audio_model.created_at),
        )

    transcription_stmt = (
        select(TranscribeTranscription)
        .where(TranscribeTranscription.session_id == model.id)
        .limit(1)
    )
    transcription_model = await session.scalar(transcription_stmt)
    draft_stmt = (
        select(TranscribeDraftDocument)
        .where(TranscribeDraftDocument.session_id == model.id)
        .limit(1)
    )
    draft_model = await session.scalar(draft_stmt)
    draft_document = None
    if draft_model is not None:
        draft_document = SessionTranscriptionDraftDocument.model_validate(
            json.loads(draft_model.content_json)
        )
    transcription = None
    if transcription_model is not None:
        if transcription_model.transcript_segments_json:
            segment_payloads = json.loads(transcription_model.transcript_segments_json)
            transcript_segments = [
                TranscriptSegment.model_validate(segment)
                for segment in segment_payloads
            ]
        elif transcription_model.transcript_text:
            transcript_segments = [
                TranscriptSegment(
                    id=str(uuid.uuid4()),
                    speaker="unknown",
                    speakerLabel="Consultation",
                    text=transcription_model.transcript_text,
                    startedAtSeconds=0,
                    endedAtSeconds=0,
                )
            ]
        else:
            transcript_segments = []
        transcription = SessionTranscription(
            status=transcription_model.status,
            providerName=transcription_model.provider_name,
            requestedAt=_serialize_datetime(transcription_model.created_at),
            completedAt=(
                _serialize_datetime(transcription_model.updated_at)
                if transcription_model.status == "completed"
                else None
            ),
            stage=(
                transcription_model.stage
                if transcription_model.status == "processing"
                else None
            )
            or (
                "transcribing_conversation"
                if transcription_model.status == "processing"
                else None
            ),
            summary=transcription_model.summary
            or (
                transcription_model.transcript_text.splitlines()[0]
                if transcription_model.transcript_text
                else None
            ),
            transcriptText=transcription_model.transcript_text,
            transcriptSegments=transcript_segments,
            requiresRecovery=transcription_model.requires_recovery,
            draftDocument=draft_document,
        )

    artifact_stmt = (
        select(TranscribeArtifact, StoredFile)
        .join(StoredFile, StoredFile.id == TranscribeArtifact.stored_file_id)
        .where(
            TranscribeArtifact.session_id == model.id,
            TranscribeArtifact.artifact_type == "pdf",
        )
        .order_by(TranscribeArtifact.created_at.desc())
        .limit(1)
    )
    artifact_row = (await session.execute(artifact_stmt)).first()
    document_artifact = None
    if artifact_row is not None:
        artifact_model, file_model = artifact_row
        document_artifact = DocumentArtifact(
            id=encode_public_id("document_artifact", artifact_model.id),
            fileName=file_model.original_filename,
            mimeType=file_model.content_type,
            sizeBytes=file_model.size_bytes,
            createdAt=_serialize_datetime(artifact_model.created_at),
            delivery=DocumentArtifactDelivery(
                shareTitle="Consultation Summary",
                shareMessage=f"Share {file_model.original_filename} with {model.patient_name or 'the patient'} after review.",
                whatsappMessage=f"Please find your consultation summary attached for {model.patient_name or 'the patient'}.",
            ),
        )

    report_doc_stmt = select(TranscribeReportContextDocument).where(
        TranscribeReportContextDocument.session_id == model.id
    )
    report_docs = list((await session.scalars(report_doc_stmt)).all())
    report_context = None
    if report_docs:
        doc_ids = [doc.id for doc in report_docs]
        stored_file_stmt = select(StoredFile).where(
            StoredFile.id.in_(
                [
                    doc.stored_file_id
                    for doc in report_docs
                    if doc.stored_file_id is not None
                ]
            )
        )
        stored_files = {
            file_model.id: file_model
            for file_model in (await session.scalars(stored_file_stmt)).all()
        }
        doc_by_id = {doc.id: doc for doc in report_docs}
        items_stmt = (
            select(TranscribeReportContextItem)
            .where(TranscribeReportContextItem.document_id.in_(doc_ids))
            .order_by(
                TranscribeReportContextItem.document_id.asc(),
                TranscribeReportContextItem.page_number.asc().nullsfirst(),
                TranscribeReportContextItem.id.asc(),
            )
        )
        items = list((await session.scalars(items_stmt)).all())
        chunks = []
        for item in items:
            document = doc_by_id.get(item.document_id)
            stored_file = (
                stored_files.get(document.stored_file_id)
                if document is not None and document.stored_file_id is not None
                else None
            )
            created_at = (
                document.created_at if document is not None else model.updated_at
            )
            chunks.append(_report_context_chunk_payload(item, stored_file, created_at))
        state = _normalize_report_context_state(
            _parse_report_context_state(model.report_context_state_json)
        )
        progress = None
        if isinstance(state.get("progress"), dict):
            try:
                progress = ReportContextProgress.model_validate(state["progress"])
            except Exception:
                progress = None
        status = cast(str, state.get("status") or "ready")
        if any(doc.status == "processing" for doc in report_docs):
            status = "processing"
        report_context = SessionReportContext(
            status=status,
            chunks=chunks,
            selectedChunkIds=cast(list[str], state.get("selectedChunkIds") or []),
            appliedChunkIds=cast(list[str], state.get("appliedChunkIds") or []),
            extractedUploadIds=cast(
                list[str],
                state.get("extractedUploadIds")
                or [
                    encode_public_id("report_upload", doc.id)
                    for doc in report_docs
                    if doc.status == "ready"
                ],
            ),
            progress=progress,
            updatedAt=(
                datetime.fromisoformat(str(state["updatedAt"]))
                if state.get("updatedAt")
                else _serialize_datetime(model.updated_at)
            ),
            error=cast(str | None, state.get("error")),
        )

    return ConsultationSession(
        id=encode_public_id("session", model.id),
        doctorId=_encode_doctor_id(model.doctor_id),
        doctor=_doctor_summary_from_model(doctor_model),
        patient=PatientSummary(
            name=model.patient_name or "",
            phoneNumber=model.patient_phone_number or "",
            age=model.patient_age,
        ),
        caseStatus=cast(Literal["open", "closed"], model.case_status),
        status=model.status,
        createdAt=_serialize_datetime(model.created_at),
        updatedAt=_serialize_datetime(model.updated_at),
        audioArtifact=audio_artifact,
        transcription=transcription,
        documentArtifact=document_artifact,
        reportContext=report_context,
    )


class SqlDoctorRecord:
    def __init__(self, model: User) -> None:
        self.profile = _doctor_profile_from_model(model)
        self.pin = model.legacy_pin_code or "123456"
        self.token_version = model.legacy_token_version
        self.must_change_pin = model.must_change_pin


class SqlDoctorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get_or_create_transcribe_org(self) -> Organization:
        org = await self.session.scalar(
            select(Organization).where(Organization.slug == TRANSCRIBE_ORG_SLUG)
        )
        if org is not None:
            return org
        org = Organization(
            name="Transcribe",
            slug=TRANSCRIBE_ORG_SLUG,
            type="hospital",
            is_active=True,
        )
        self.session.add(org)
        await self.session.flush()
        return org

    def _doctor_query(self):
        return select(User).where(User.role == "doctor", User.is_active.is_(True))

    async def seed_defaults(self) -> None:
        existing = await self.session.scalar(self._doctor_query().limit(1))
        if existing is not None:
            return

        org = await self._get_or_create_transcribe_org()
        now = datetime.now(UTC)
        for index, doctor in enumerate(PRELOADED_DOCTORS, start=1):
            self.session.add(
                User(
                    organization_id=org.id,
                    email=f"transcribe-doctor-{index}@fettle.local",
                    password_hash=hash_password(secrets.token_urlsafe(16)),
                    name=doctor["name"],
                    role="doctor",
                    department=doctor["department"],
                    mobile_number=None,
                    last_login_at=now,
                    legacy_pin_code="123456",
                    legacy_token_version=0,
                    must_change_pin=False,
                    is_active=True,
                )
            )
        await self.session.commit()

    async def list_doctors(
        self, search: str | None = None
    ) -> list[PublicDoctorProfile]:
        stmt = self._doctor_query()
        normalized = (search or "").strip()
        if normalized:
            like = f"%{normalized.lower()}%"
            stmt = stmt.where(
                or_(
                    User.name.ilike(like),
                    User.department.ilike(like),
                )
            )
        stmt = stmt.order_by(User.name.asc())
        doctors = (await self.session.scalars(stmt)).all()
        return [_public_doctor_profile_from_model(doctor) for doctor in doctors]

    async def list_doctor_profiles(
        self, search: str | None = None
    ) -> list[DoctorProfile]:
        stmt = self._doctor_query()
        normalized = (search or "").strip()
        if normalized:
            like = f"%{normalized.lower()}%"
            stmt = stmt.where(
                or_(
                    User.name.ilike(like),
                    User.department.ilike(like),
                )
            )
        stmt = stmt.order_by(User.name.asc())
        doctors = (await self.session.scalars(stmt)).all()
        return [_doctor_profile_from_model(doctor) for doctor in doctors]

    async def find_by_id(self, doctor_id: str) -> SqlDoctorRecord | None:
        try:
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        doctor = await self.session.get(User, doctor_pk)
        if doctor is None or not doctor.is_active:
            return None
        return SqlDoctorRecord(doctor)

    async def find_by_public_path(
        self, department_slug: str, doctor_name_slug: str
    ) -> SqlDoctorRecord | None:
        doctors = list((await self.session.scalars(self._doctor_query())).all())
        for doctor in doctors:
            if (
                _slugify(doctor.department or "general") == department_slug
                and _slugify(doctor.name) == doctor_name_slug
            ):
                return SqlDoctorRecord(doctor)
        return None

    async def get_profile_by_id(self, doctor_id: str) -> DoctorProfile | None:
        record = await self.find_by_id(doctor_id)
        return record.profile if record is not None else None

    async def create_doctor(
        self,
        *,
        name: str,
        department: str | None,
        phone_number: str | None,
        email: str | None,
    ) -> DoctorProfile:
        now = datetime.now(UTC)
        org = await self._get_or_create_transcribe_org()
        stmt = self._doctor_query().where(User.organization_id == org.id)
        doctors = list((await self.session.scalars(stmt)).all())
        existing = next(
            (
                doctor
                for doctor in doctors
                if _slugify(doctor.department or "general")
                == _slugify(department or "general")
                and _slugify(doctor.name) == _slugify(name)
            ),
            None,
        )
        if existing is None and phone_number:
            existing = next(
                (doctor for doctor in doctors if doctor.mobile_number == phone_number),
                None,
            )
        if existing is None and email:
            normalized_email = email.lower()
            existing = next(
                (doctor for doctor in doctors if doctor.email == normalized_email), None
            )
        if existing is not None:
            existing.name = name
            existing.department = department
            existing.mobile_number = phone_number
            existing.email = (email or existing.email).lower()
            existing.legacy_pin_code = "123456"
            existing.must_change_pin = False
            existing.last_login_at = now
            await self.session.commit()
            await self.session.refresh(existing)
            return _doctor_profile_from_model(existing)
        normalized_email = (
            email.lower()
            if email
            else f"{_slugify(name) or 'doctor'}-{secrets.token_hex(4)}@fettle.local"
        )
        model = User(
            organization_id=org.id,
            email=normalized_email,
            password_hash=hash_password(secrets.token_urlsafe(16)),
            name=name,
            role="doctor",
            department=department,
            mobile_number=phone_number,
            last_login_at=now,
            legacy_pin_code="123456",
            legacy_token_version=0,
            must_change_pin=False,
            is_active=True,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return _doctor_profile_from_model(model)

    async def update_doctor(
        self,
        doctor_id: str,
        *,
        name: str,
        department: str | None,
        phone_number: str | None,
        email: str | None,
    ) -> DoctorProfile | None:
        try:
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        model = await self.session.get(User, doctor_pk)
        if model is None or not model.is_active:
            return None
        model.name = name
        model.department = department
        model.mobile_number = phone_number
        model.email = email.lower() if email else model.email
        await self.session.commit()
        await self.session.refresh(model)
        return _doctor_profile_from_model(model)

    async def reset_pin(
        self, doctor_id: str, temporary_pin: str
    ) -> DoctorProfile | None:
        try:
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        model = await self.session.get(User, doctor_pk)
        if model is None or not model.is_active:
            return None
        model.legacy_pin_code = temporary_pin
        model.must_change_pin = True
        model.legacy_token_version += 1
        await self.session.commit()
        await self.session.refresh(model)
        return _doctor_profile_from_model(model)

    async def clear_pin_lock(self, doctor_id: str) -> DoctorProfile | None:
        return await self.get_profile_by_id(doctor_id)

    async def set_pin(self, doctor_id: str, new_pin: str) -> DoctorProfile | None:
        try:
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        model = await self.session.get(User, doctor_pk)
        if model is None or not model.is_active:
            return None
        model.legacy_pin_code = new_pin
        model.must_change_pin = False
        model.legacy_token_version += 1
        await self.session.commit()
        await self.session.refresh(model)
        return _doctor_profile_from_model(model)

    async def reset_runtime_state(self) -> None:
        doctors = (await self.session.scalars(self._doctor_query())).all()
        now = datetime.now(UTC)
        for doctor in doctors:
            doctor.legacy_pin_code = "123456"
            doctor.must_change_pin = False
            doctor.last_login_at = now
        await self.session.commit()

    async def touch_last_login(self, doctor_id: str) -> SqlDoctorRecord | None:
        try:
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        doctor = await self.session.get(User, doctor_pk)
        if doctor is None:
            return None
        doctor.last_login_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(doctor)
        return SqlDoctorRecord(doctor)


class SqlSyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, job: dict) -> dict:
        existing = await self.session.scalar(
            select(TranscribeSyncJob).where(
                TranscribeSyncJob.idempotency_key == job["idempotencyKey"]
            )
        )
        if existing is not None:
            return _sync_job_payload(existing)
        session_id = str(job.get("sessionId", ""))
        model = TranscribeSyncJob(
            session_id=(
                _decode_session_id(session_id)
                if session_id not in {"", "dry-run"}
                else None
            ),
            status="queued",
            trigger=job["trigger"],
            idempotency_key=job["idempotencyKey"],
            external_session_id=session_id,
            payload_json=json.dumps(job["payload"]),
            attempts=0,
            max_attempts=int(job["maxAttempts"]),
            first_queued_at=job["queuedAt"],
            next_attempt_at=job["queuedAt"],
            updated_at=job["queuedAt"],
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return _sync_job_payload(model)

    async def get_stats(self, enabled: bool) -> dict:
        rows = (
            await self.session.execute(
                select(
                    TranscribeSyncJob.status, func.count(TranscribeSyncJob.id)
                ).group_by(TranscribeSyncJob.status)
            )
        ).all()
        counts = {status: count for status, count in rows}
        last_updated_at = await self.session.scalar(
            select(func.max(TranscribeSyncJob.updated_at))
        )
        return {
            "enabled": enabled,
            "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0),
            "succeeded": counts.get("succeeded", 0),
            "deadLetter": counts.get("dead_letter", 0),
            "retryable": counts.get("queued", 0) + counts.get("processing", 0),
            **(
                {"lastUpdatedAt": last_updated_at.isoformat()}
                if last_updated_at is not None
                else {}
            ),
        }

    async def list_jobs(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict]:
        stmt = select(TranscribeSyncJob)
        if status:
            stmt = stmt.where(TranscribeSyncJob.status == status)
        stmt = stmt.order_by(TranscribeSyncJob.updated_at.desc()).limit(limit)
        jobs = (await self.session.scalars(stmt)).all()
        return [_sync_job_payload(job) for job in jobs]

    async def claim_due_jobs(self, limit: int, now: datetime) -> list[dict]:
        stmt = (
            select(TranscribeSyncJob)
            .where(
                TranscribeSyncJob.status == "queued",
                TranscribeSyncJob.next_attempt_at <= now,
            )
            .order_by(TranscribeSyncJob.next_attempt_at.asc())
            .limit(limit)
        )
        jobs = list((await self.session.scalars(stmt)).all())
        for job in jobs:
            job.status = "processing"
            job.attempts += 1
            job.updated_at = now
        await self.session.commit()
        return [
            {
                **_sync_job_payload(job),
                "payload": json.loads(job.payload_json),
                "idempotencyKey": job.idempotency_key,
            }
            for job in jobs
        ]

    async def mark_succeeded(self, job_id: str, finished_at: datetime) -> None:
        job = await self.session.get(TranscribeSyncJob, _decode_sync_job_id(job_id))
        if job is None:
            return
        job.status = "succeeded"
        job.updated_at = finished_at
        job.finished_at = finished_at
        job.last_error = None
        job.last_error_class = None
        await self.session.commit()

    async def mark_failed(
        self,
        job_id: str,
        *,
        status: str,
        next_attempt_at: datetime,
        last_error: str,
        last_error_class: str,
        finished_at: datetime | None,
    ) -> None:
        job = await self.session.get(TranscribeSyncJob, _decode_sync_job_id(job_id))
        if job is None:
            return
        job.status = status
        job.next_attempt_at = next_attempt_at
        job.updated_at = datetime.now(UTC)
        job.last_error = last_error
        job.last_error_class = last_error_class
        job.finished_at = finished_at
        await self.session.commit()

    async def replay_dead_letter(self, job_id: str, now: datetime) -> dict | None:
        try:
            job_pk = _decode_sync_job_id(job_id)
        except ValueError:
            return None
        job = await self.session.get(TranscribeSyncJob, job_pk)
        if job is None or job.status != "dead_letter":
            return None
        job.status = "queued"
        job.attempts = 0
        job.next_attempt_at = now
        job.updated_at = now
        job.last_error = None
        job.last_error_class = None
        job.finished_at = None
        await self.session.commit()
        await self.session.refresh(job)
        return _sync_job_payload(job)


class SqlSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self,
        doctor_id: str,
        patient_name: str,
        patient_phone_number: str,
        patient_age: int | None,
    ) -> ConsultationSession:
        model = TranscribeSession(
            doctor_id=_decode_doctor_id(doctor_id),
            patient_name=patient_name,
            patient_phone_number=patient_phone_number,
            patient_age=patient_age,
            case_status="open",
            status="draft",
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return await _session_from_model(self.session, model)

    async def list_sessions(
        self, doctor_id: str, search: str | None = None
    ) -> list[ConsultationSession]:
        stmt = select(TranscribeSession).where(
            TranscribeSession.doctor_id == _decode_doctor_id(doctor_id)
        )
        normalized = (search or "").strip()
        if normalized:
            like = f"%{normalized.lower()}%"
            stmt = stmt.where(
                or_(
                    TranscribeSession.patient_name.ilike(like),
                    TranscribeSession.patient_phone_number.ilike(like),
                )
            )
        stmt = stmt.order_by(TranscribeSession.created_at.desc())
        sessions = (await self.session.scalars(stmt)).all()
        return [await _session_from_model(self.session, item) for item in sessions]

    async def get_session(
        self, session_id: str, doctor_id: str
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        stmt = select(TranscribeSession).where(
            TranscribeSession.id == session_pk,
            TranscribeSession.doctor_id == doctor_pk,
        )
        model = await self.session.scalar(stmt)
        return await _session_from_model(self.session, model) if model else None

    async def get_session_any(self, session_id: str) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        model = await self.session.get(TranscribeSession, session_pk)
        return await _session_from_model(self.session, model) if model else None

    async def get_patient_history(
        self, session_id: str, doctor_id: str
    ) -> list[ConsultationSession]:
        current = await self.get_session(session_id, doctor_id)
        if current is None:
            return []
        stmt = (
            select(TranscribeSession)
            .where(
                TranscribeSession.doctor_id == _decode_doctor_id(doctor_id),
                TranscribeSession.patient_phone_number == current.patient.phoneNumber,
                TranscribeSession.id != _decode_session_id(session_id),
                TranscribeSession.created_at < current.createdAt,
            )
            .order_by(TranscribeSession.created_at.desc())
            .limit(10)
        )
        sessions = (await self.session.scalars(stmt)).all()
        return [await _session_from_model(self.session, item) for item in sessions]

    async def list_sessions_admin(
        self,
        *,
        doctor_id: str | None = None,
        search: str | None = None,
    ) -> list[ConsultationSession]:
        stmt = select(TranscribeSession)
        if doctor_id is not None:
            stmt = stmt.where(
                TranscribeSession.doctor_id == _decode_doctor_id(doctor_id)
            )
        normalized = (search or "").strip()
        if normalized:
            like = f"%{normalized.lower()}%"
            stmt = stmt.where(
                or_(
                    TranscribeSession.patient_name.ilike(like),
                    TranscribeSession.patient_phone_number.ilike(like),
                )
            )
        stmt = stmt.order_by(TranscribeSession.created_at.desc())
        sessions = (await self.session.scalars(stmt)).all()
        return [await _session_from_model(self.session, item) for item in sessions]

    async def get_patient_profile_by_phone(
        self, phone_number: str, doctor_id: str
    ) -> PatientProfile | None:
        stmt = (
            select(TranscribeSession)
            .where(
                TranscribeSession.doctor_id == _decode_doctor_id(doctor_id),
                TranscribeSession.patient_phone_number == phone_number,
            )
            .order_by(TranscribeSession.created_at.desc())
            .limit(1)
        )
        model = await self.session.scalar(stmt)
        if model is None or not model.patient_name or not model.patient_phone_number:
            return None
        return PatientProfile(
            name=model.patient_name,
            phoneNumber=model.patient_phone_number,
            age=model.patient_age,
        )

    async def update_case_status(
        self, session_id: str, doctor_id: str, case_status: str
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        stmt = select(TranscribeSession).where(
            TranscribeSession.id == session_pk,
            TranscribeSession.doctor_id == doctor_pk,
        )
        model = await self.session.scalar(stmt)
        if model is None:
            return None
        model.case_status = case_status
        model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(model)
        return await _session_from_model(self.session, model)

    async def reset_sessions(self) -> None:
        await self.session.execute(
            delete(Job).where(
                Job.product == "transcribe",
                Job.job_type == "report_context_extract",
            )
        )
        await self.session.execute(delete(KpiEvent))
        await self.session.execute(delete(TranscribeSyncJob))
        await self.session.execute(delete(TranscribePdfDeliveryJob))
        await self.session.execute(delete(TranscribePdfDeliverySettings))
        await self.session.execute(delete(TranscribeQueueEntry))
        await self.session.execute(delete(TranscribeIntakeLink))
        await self.session.execute(delete(TranscribeReportContextItem))
        await self.session.execute(delete(TranscribeReportContextDocument))
        await self.session.execute(delete(TranscribeSessionAudio))
        await self.session.execute(delete(TranscribeArtifact))
        await self.session.execute(delete(TranscribeTranscription))
        await self.session.execute(delete(TranscribeDraftDocument))
        await self.session.execute(delete(TranscribeSession))
        await self.session.commit()

    async def store_audio(
        self,
        session_id: str,
        doctor_id: str,
        *,
        file_name: str,
        mime_type: str,
        duration_seconds: float,
        size_bytes: int,
        storage_key: str,
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        session_stmt = select(TranscribeSession).where(
            TranscribeSession.id == session_pk, TranscribeSession.doctor_id == doctor_pk
        )
        session_model = await self.session.scalar(session_stmt)
        if session_model is None:
            return None
        stored_file = StoredFile(
            storage_key=storage_key,
            original_filename=file_name,
            content_type=mime_type,
            size_bytes=size_bytes,
        )
        self.session.add(stored_file)
        await self.session.flush()
        audio = TranscribeSessionAudio(
            session_id=session_model.id,
            stored_file_id=stored_file.id,
            duration_seconds=int(duration_seconds),
            size_bytes=size_bytes,
        )
        self.session.add(audio)
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def mark_transcription_processing(
        self,
        session_id: str,
        doctor_id: str,
        *,
        provider_name: str,
        idempotency_key: str | None,
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        session_stmt = select(TranscribeSession).where(
            TranscribeSession.id == session_pk, TranscribeSession.doctor_id == doctor_pk
        )
        session_model = await self.session.scalar(session_stmt)
        if session_model is None:
            return None
        stmt = select(TranscribeTranscription).where(
            TranscribeTranscription.session_id == session_model.id
        )
        transcription = await self.session.scalar(stmt)
        if transcription is None:
            transcription = TranscribeTranscription(
                session_id=session_model.id,
                status="processing",
                provider_name=provider_name,
                idempotency_key=idempotency_key,
                stage="transcribing_conversation",
                attempt_count=1,
            )
            self.session.add(transcription)
        else:
            if (
                transcription.status == "processing"
                and transcription.idempotency_key == idempotency_key
            ):
                return await _session_from_model(self.session, session_model)
            transcription.status = "processing"
            transcription.provider_name = provider_name
            transcription.idempotency_key = idempotency_key
            transcription.stage = "transcribing_conversation"
            transcription.attempt_count += 1
        session_model.status = "processing"
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def complete_transcription(
        self, session_id: str, result: dict
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        session_model = await self.session.get(TranscribeSession, session_pk)
        if session_model is None:
            return None
        transcription_stmt = select(TranscribeTranscription).where(
            TranscribeTranscription.session_id == session_model.id
        )
        transcription = await self.session.scalar(transcription_stmt)
        if transcription is None:
            transcription = TranscribeTranscription(
                session_id=session_model.id,
                status="completed",
                provider_name=str(result.get("provider", "mock-gemini")),
            )
            self.session.add(transcription)
        segments = list(result.get("transcriptSegments", []))
        summary = str(result.get("summary", ""))
        draft_document = dict(result.get("draftDocument", {}))
        draft_stmt = select(TranscribeDraftDocument).where(
            TranscribeDraftDocument.session_id == session_model.id
        )
        draft_model = await self.session.scalar(draft_stmt)
        draft_json = json.dumps(draft_document)
        if draft_model is None:
            draft_model = TranscribeDraftDocument(
                session_id=session_model.id, content_json=draft_json
            )
            self.session.add(draft_model)
        else:
            draft_model.content_json = draft_json
            draft_model.updated_at = datetime.now(UTC)
        transcription.status = "completed"
        transcription.provider_name = str(
            result.get("provider", transcription.provider_name or "mock-gemini")
        )
        transcription.summary = summary
        transcription.transcript_segments_json = json.dumps(segments)
        transcription.metadata_json = json.dumps(result.get("metadata", {}))
        transcription.requires_recovery = bool(result.get("requiresRecovery", False))
        transcription.stage = "completed"
        transcription.transcript_text = "\n".join(
            str(segment.get("text", "")) for segment in segments
        ).strip()
        transcription.updated_at = datetime.now(UTC)
        session_model.status = "completed"
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def fail_transcription(
        self,
        session_id: str,
        error_message: str,
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        session_model = await self.session.get(TranscribeSession, session_pk)
        if session_model is None:
            return None
        transcription_stmt = select(TranscribeTranscription).where(
            TranscribeTranscription.session_id == session_model.id
        )
        transcription = await self.session.scalar(transcription_stmt)
        if transcription is None:
            transcription = TranscribeTranscription(
                session_id=session_model.id,
                status="failed",
                provider_name="unknown",
            )
            self.session.add(transcription)
        transcription.status = "failed"
        transcription.summary = None
        transcription.stage = "failed"
        transcription.transcript_text = error_message[:1000]
        transcription.updated_at = datetime.now(UTC)
        session_model.status = "failed"
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def update_draft_document(
        self, session_id: str, doctor_id: str, draft_document: dict
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        session_stmt = select(TranscribeSession).where(
            TranscribeSession.id == session_pk, TranscribeSession.doctor_id == doctor_pk
        )
        session_model = await self.session.scalar(session_stmt)
        if session_model is None:
            return None
        draft_stmt = select(TranscribeDraftDocument).where(
            TranscribeDraftDocument.session_id == session_model.id
        )
        draft_model = await self.session.scalar(draft_stmt)
        if draft_model is None:
            draft_model = TranscribeDraftDocument(
                session_id=session_model.id,
                content_json=json.dumps(draft_document),
            )
            self.session.add(draft_model)
        else:
            draft_model.content_json = json.dumps(draft_document)
            draft_model.updated_at = datetime.now(UTC)
        transcription_stmt = select(TranscribeTranscription).where(
            TranscribeTranscription.session_id == session_model.id
        )
        transcription = await self.session.scalar(transcription_stmt)
        if transcription is None:
            transcription = TranscribeTranscription(
                session_id=session_model.id,
                status="draft_ready",
                provider_name="manual-draft",
            )
            self.session.add(transcription)
        elif transcription.status == "processing":
            transcription.status = "draft_ready"
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def store_generated_pdf(
        self,
        session_id: str,
        doctor_id: str,
        *,
        file_name: str,
        storage_key: str,
        size_bytes: int,
    ) -> ConsultationSession | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        stmt = select(TranscribeSession).where(
            TranscribeSession.id == session_pk,
            TranscribeSession.doctor_id == doctor_pk,
        )
        session_model = await self.session.scalar(stmt)
        if session_model is None:
            return None
        stored_file = StoredFile(
            storage_key=storage_key,
            original_filename=file_name,
            content_type="application/pdf",
            size_bytes=size_bytes,
        )
        self.session.add(stored_file)
        await self.session.flush()
        artifact = await self.session.scalar(
            select(TranscribeArtifact).where(
                TranscribeArtifact.session_id == session_model.id,
                TranscribeArtifact.artifact_type == "pdf",
            )
        )
        if artifact is None:
            artifact = TranscribeArtifact(
                session_id=session_model.id,
                artifact_type="pdf",
                stored_file_id=stored_file.id,
            )
            self.session.add(artifact)
        else:
            artifact.stored_file_id = stored_file.id
        session_model.updated_at = datetime.now(UTC)
        await self._enqueue_pdf_delivery_jobs_for_model(session_model)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def save_pdf_delivery_settings(
        self,
        session_id: str,
        *,
        patient_enabled: bool,
        pharmacy_enabled: bool,
        pharmacy: dict | None,
    ) -> dict | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        session_model = await self.session.get(TranscribeSession, session_pk)
        if session_model is None:
            return None
        model = await self.session.scalar(
            select(TranscribePdfDeliverySettings).where(
                TranscribePdfDeliverySettings.session_id == session_model.id
            )
        )
        if model is None:
            model = TranscribePdfDeliverySettings(session_id=session_model.id)
            self.session.add(model)
        model.patient_enabled = patient_enabled
        model.pharmacy_enabled = pharmacy_enabled
        model.pharmacy_name = (
            str(pharmacy.get("name", "")).strip() if pharmacy else None
        )
        model.pharmacy_phone_number = (
            str(pharmacy.get("phoneNumber", "")).strip() if pharmacy else None
        )
        model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(model)
        return _pdf_delivery_settings_payload(model)

    async def get_pdf_delivery_status(self, session_id: str) -> dict | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        session_model = await self.session.get(TranscribeSession, session_pk)
        if session_model is None:
            return None
        settings = await self.session.scalar(
            select(TranscribePdfDeliverySettings).where(
                TranscribePdfDeliverySettings.session_id == session_model.id
            )
        )
        return {
            "settings": _pdf_delivery_settings_payload(settings),
            "jobs": await self.list_pdf_delivery_jobs_by_session(session_id),
        }

    async def enqueue_pdf_delivery_jobs(self, session_id: str) -> list[dict] | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        session_model = await self.session.get(TranscribeSession, session_pk)
        if session_model is None:
            return None
        jobs = await self._enqueue_pdf_delivery_jobs_for_model(session_model)
        await self.session.commit()
        return jobs

    async def list_pdf_delivery_jobs_by_session(self, session_id: str) -> list[dict]:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return []
        stmt = (
            select(TranscribePdfDeliveryJob)
            .where(TranscribePdfDeliveryJob.session_id == session_pk)
            .order_by(TranscribePdfDeliveryJob.created_at.desc())
        )
        jobs = (await self.session.scalars(stmt)).all()
        return [_pdf_delivery_job_payload(job) for job in jobs]

    async def list_pdf_delivery_jobs_admin(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        recipient_type: str | None = None,
    ) -> list[dict]:
        stmt = select(TranscribePdfDeliveryJob)
        if session_id:
            stmt = stmt.where(
                TranscribePdfDeliveryJob.session_id == _decode_session_id(session_id)
            )
        if status:
            stmt = stmt.where(TranscribePdfDeliveryJob.status == status)
        if recipient_type:
            stmt = stmt.where(TranscribePdfDeliveryJob.recipient_type == recipient_type)
        stmt = stmt.order_by(TranscribePdfDeliveryJob.created_at.desc())
        jobs = (await self.session.scalars(stmt)).all()
        return [_pdf_delivery_job_payload(job) for job in jobs]

    async def get_pdf_delivery_job(self, job_id: str) -> dict | None:
        try:
            job_pk = _decode_pdf_delivery_job_id(job_id)
        except ValueError:
            return None
        job = await self.session.get(TranscribePdfDeliveryJob, job_pk)
        return _pdf_delivery_job_payload(job) if job is not None else None

    async def claim_pending_pdf_delivery_jobs(self, limit: int) -> list[dict]:
        stmt = (
            select(TranscribePdfDeliveryJob)
            .where(TranscribePdfDeliveryJob.status == "pending")
            .order_by(TranscribePdfDeliveryJob.created_at.asc())
            .limit(limit)
        )
        jobs = list((await self.session.scalars(stmt)).all())
        now = datetime.now(UTC)
        for job in jobs:
            job.status = "processing"
            job.attempt_count += 1
            job.updated_at = now
        await self.session.commit()
        return [_pdf_delivery_job_payload(job) for job in jobs]

    async def update_pdf_delivery_job_status(
        self,
        job_id: str,
        status: str,
        *,
        provider: str | None = None,
        provider_message_id: str | None = None,
        last_error: str | None = None,
        sent_at: datetime | None = None,
    ) -> dict | None:
        try:
            job_pk = _decode_pdf_delivery_job_id(job_id)
        except ValueError:
            return None
        job = await self.session.get(TranscribePdfDeliveryJob, job_pk)
        if job is None:
            return None
        job.status = status
        job.provider = provider or job.provider
        job.provider_message_id = provider_message_id or job.provider_message_id
        job.last_error = last_error
        job.sent_at = sent_at or job.sent_at
        job.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(job)
        return _pdf_delivery_job_payload(job)

    async def get_pdf_delivery_job_by_provider_message_id(
        self, provider_message_id: str
    ) -> dict | None:
        stmt = select(TranscribePdfDeliveryJob).where(
            TranscribePdfDeliveryJob.provider_message_id == provider_message_id
        )
        job = await self.session.scalar(stmt)
        return _pdf_delivery_job_payload(job) if job is not None else None

    async def _enqueue_pdf_delivery_jobs_for_model(
        self, session_model: TranscribeSession
    ) -> list[dict]:
        settings = await self.session.scalar(
            select(TranscribePdfDeliverySettings).where(
                TranscribePdfDeliverySettings.session_id == session_model.id
            )
        )
        drafts: list[dict[str, str]] = []
        settings_payload = _pdf_delivery_settings_payload(settings)
        if settings_payload["patientEnabled"]:
            drafts.append(
                {
                    "recipient_type": "patient",
                    "recipient_name": session_model.patient_name or "Patient",
                    "phone_number": session_model.patient_phone_number or "",
                }
            )
        pharmacy = settings_payload.get("pharmacy")
        if settings_payload["pharmacyEnabled"] and isinstance(pharmacy, dict):
            drafts.append(
                {
                    "recipient_type": "pharmacy",
                    "recipient_name": str(pharmacy.get("name", "")),
                    "phone_number": str(pharmacy.get("phoneNumber", "")),
                }
            )
        if not drafts:
            return []
        existing = list(
            (
                await self.session.scalars(
                    select(TranscribePdfDeliveryJob).where(
                        TranscribePdfDeliveryJob.session_id == session_model.id
                    )
                )
            ).all()
        )
        now = datetime.now(UTC)
        created: list[TranscribePdfDeliveryJob] = []
        for draft in drafts:
            if any(
                self._blocks_pdf_delivery_enqueue(job, draft["recipient_type"], now)
                for job in existing
            ):
                continue
            job = TranscribePdfDeliveryJob(
                session_id=session_model.id,
                destination=draft["phone_number"],
                recipient_type=draft["recipient_type"],
                recipient_name=draft["recipient_name"],
                phone_number=draft["phone_number"],
                status="pending",
                attempt_count=0,
                updated_at=now,
            )
            self.session.add(job)
            await self.session.flush()
            existing.append(job)
            created.append(job)
        return [_pdf_delivery_job_payload(job) for job in created]

    def _blocks_pdf_delivery_enqueue(
        self, job: TranscribePdfDeliveryJob, recipient_type: str, now: datetime
    ) -> bool:
        if job.recipient_type != recipient_type:
            return False
        if job.status in {"sent", "delivered", "read", "failed"}:
            return False
        if job.status in {"pending", "processing"}:
            age_seconds = (now - job.updated_at).total_seconds()
            return age_seconds <= PDF_DELIVERY_BLOCK_TTL_SECONDS
        if job.status == "queued":
            age_seconds = (now - job.updated_at).total_seconds()
            return age_seconds <= PDF_DELIVERY_BLOCK_TTL_SECONDS
        return False

    async def get_audio_download(
        self, session_id: str, doctor_id: str
    ) -> tuple[StoredFile, TranscribeSessionAudio] | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        stmt = (
            select(StoredFile, TranscribeSessionAudio)
            .join(
                TranscribeSessionAudio,
                StoredFile.id == TranscribeSessionAudio.stored_file_id,
            )
            .join(
                TranscribeSession,
                TranscribeSession.id == TranscribeSessionAudio.session_id,
            )
            .where(
                TranscribeSession.id == session_pk,
                TranscribeSession.doctor_id == doctor_pk,
            )
            .order_by(TranscribeSessionAudio.created_at.desc())
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    async def get_audio_download_any(
        self, session_id: str
    ) -> tuple[StoredFile, TranscribeSessionAudio] | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        stmt = (
            select(StoredFile, TranscribeSessionAudio)
            .join(
                TranscribeSessionAudio,
                StoredFile.id == TranscribeSessionAudio.stored_file_id,
            )
            .where(TranscribeSessionAudio.session_id == session_pk)
            .order_by(TranscribeSessionAudio.created_at.desc())
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    async def get_pdf_download(
        self, session_id: str, doctor_id: str
    ) -> StoredFile | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        stmt = (
            select(StoredFile)
            .join(
                TranscribeArtifact, StoredFile.id == TranscribeArtifact.stored_file_id
            )
            .join(
                TranscribeSession, TranscribeSession.id == TranscribeArtifact.session_id
            )
            .where(
                TranscribeSession.id == session_pk,
                TranscribeSession.doctor_id == doctor_pk,
                TranscribeArtifact.artifact_type == "pdf",
            )
            .order_by(TranscribeArtifact.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def get_pdf_download_any(self, session_id: str) -> StoredFile | None:
        try:
            session_pk = _decode_session_id(session_id)
        except ValueError:
            return None
        stmt = (
            select(StoredFile)
            .join(
                TranscribeArtifact, StoredFile.id == TranscribeArtifact.stored_file_id
            )
            .where(
                TranscribeArtifact.session_id == session_pk,
                TranscribeArtifact.artifact_type == "pdf",
            )
            .order_by(TranscribeArtifact.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def store_report_context_upload(
        self,
        session_id: str,
        doctor_id: str,
        *,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        storage_key: str,
    ) -> dict | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        stored_file = StoredFile(
            storage_key=storage_key,
            original_filename=file_name,
            content_type=mime_type,
            size_bytes=size_bytes,
        )
        self.session.add(stored_file)
        await self.session.flush()
        document = TranscribeReportContextDocument(
            session_id=session_model.id,
            stored_file_id=stored_file.id,
            status="uploaded",
        )
        self.session.add(document)
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(document)
        return self._report_context_upload_payload(document, stored_file)

    async def list_report_context_uploads(
        self, session_id: str, doctor_id: str
    ) -> list[dict]:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return []
        stmt = (
            select(TranscribeReportContextDocument, StoredFile)
            .join(
                StoredFile,
                StoredFile.id == TranscribeReportContextDocument.stored_file_id,
            )
            .where(TranscribeReportContextDocument.session_id == session_model.id)
            .order_by(TranscribeReportContextDocument.created_at.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            self._report_context_upload_payload(document, stored_file)
            for document, stored_file in rows
        ]

    async def get_report_context_upload_content(
        self,
        session_id: str,
        upload_id: str,
        doctor_id: str,
    ) -> StoredFile | None:
        row = await self._load_report_context_upload_row(
            session_id, doctor_id, upload_id
        )
        if row is None:
            return None
        _document, stored_file = row
        return stored_file

    async def delete_report_context_upload(
        self, session_id: str, upload_id: str, doctor_id: str
    ) -> bool:
        row = await self._load_report_context_upload_row(
            session_id, doctor_id, upload_id
        )
        if row is None:
            return False
        document, stored_file = row
        await self.session.execute(
            delete(TranscribeReportContextItem).where(
                TranscribeReportContextItem.document_id == document.id
            )
        )
        await self.session.delete(document)
        await self.session.delete(stored_file)
        await self.session.commit()
        return True

    async def get_report_context_upload_blobs(
        self,
        session_id: str,
        doctor_id: str,
        upload_ids: list[str],
    ) -> list[dict] | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        documents = await self._load_report_context_documents(
            session_model.id, upload_ids
        )
        if len(documents) != len(upload_ids):
            return None
        rows: list[dict] = []
        for document in documents:
            if document.stored_file_id is None:
                return None
            stored_file = await self.session.get(StoredFile, document.stored_file_id)
            if stored_file is None:
                return None
            rows.append(
                {
                    "id": encode_public_id("report_upload", document.id),
                    "fileName": stored_file.original_filename,
                    "mimeType": stored_file.content_type,
                    "sizeBytes": stored_file.size_bytes,
                    "storageKey": stored_file.storage_key,
                }
            )
        return rows

    async def update_report_context_state(
        self,
        session_id: str,
        doctor_id: str,
        payload: dict[str, Any],
    ) -> ConsultationSession | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        session_model.report_context_state_json = json.dumps(
            _normalize_report_context_state(payload)
        )
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def finalize_report_context_extraction(
        self,
        session_id: str,
        doctor_id: str,
        upload_ids: list[str],
        chunks: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> ConsultationSession | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        documents = await self._load_report_context_documents(
            session_model.id, upload_ids
        )
        if len(documents) != len(upload_ids):
            return None
        document_by_upload_id = {
            encode_public_id("report_upload", document.id): document
            for document in documents
        }
        document_ids = [document.id for document in documents]
        await self.session.execute(
            delete(TranscribeReportContextItem).where(
                TranscribeReportContextItem.document_id.in_(document_ids)
            )
        )
        for document in documents:
            document.status = "ready" if payload.get("status") == "ready" else "failed"
        for chunk in chunks:
            upload_id = str(chunk.get("uploadId") or "")
            if upload_id not in document_by_upload_id:
                continue
            document = document_by_upload_id[upload_id]
            extracted_at = chunk.get("extractedAt")
            parsed_extracted_at: datetime | None = None
            if isinstance(extracted_at, str) and extracted_at:
                parsed_extracted_at = datetime.fromisoformat(extracted_at)
            item = TranscribeReportContextItem(
                document_id=document.id,
                source_file_name=str(chunk.get("sourceFileName") or ""),
                source_index=chunk.get("sourceIndex"),
                page_number=chunk.get("pageNumber"),
                label=str(chunk.get("title") or chunk.get("label") or ""),
                category=str(chunk.get("category") or "report_context"),
                extracted_text=str(chunk.get("text") or ""),
                extracted_at=parsed_extracted_at,
            )
            self.session.add(item)
        session_model.report_context_state_json = json.dumps(
            _normalize_report_context_state(payload)
        )
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(session_model)
        return await _session_from_model(self.session, session_model)

    async def create_report_context_job(
        self,
        session_id: str,
        doctor_id: str,
        upload_ids: list[str],
    ) -> dict | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        documents = await self._load_report_context_documents(
            session_model.id, upload_ids
        )
        if len(documents) != len(upload_ids):
            return None
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(
            {
                "sessionId": session_id,
                "doctorId": doctor_id,
                "uploadIds": upload_ids,
                "totalUnits": 0,
                "completedUnits": 0,
                "failedUnits": 0,
                "chunks": [],
                "createdAt": now,
                "updatedAt": now,
            }
        )
        job = Job(
            queue="transcribe",
            product="transcribe",
            job_type="report_context_extract",
            status="queued",
            payload_json=payload,
        )
        self.session.add(job)
        await self.session.flush()
        for document in documents:
            document.status = "uploaded"
            document.job_id = job.id
        session_model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(job)
        return await self._report_context_job_payload(job)

    async def get_report_context_job(
        self,
        session_id: str,
        doctor_id: str,
        job_id: str,
    ) -> dict | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        job = await self._load_report_context_job(session_model.id, job_id)
        if job is None:
            return None
        return await self._report_context_job_payload(job)

    async def get_active_report_context_job(
        self,
        session_id: str,
        doctor_id: str,
    ) -> dict | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        stmt = (
            select(Job)
            .where(
                Job.product == "transcribe",
                Job.job_type == "report_context_extract",
                Job.status.in_(["queued", "processing"]),
            )
            .order_by(Job.created_at.desc())
        )
        jobs = list((await self.session.scalars(stmt)).all())
        for job in jobs:
            payload = json.loads(job.payload_json)
            try:
                payload_session_pk = _decode_session_id(str(payload["sessionId"]))
            except (KeyError, TypeError, ValueError):
                continue
            if payload_session_pk == session_model.id:
                return await self._report_context_job_payload(job)
        return None

    async def list_pending_report_context_jobs(self) -> list[dict[str, Any]]:
        stmt = (
            select(Job)
            .where(
                Job.product == "transcribe",
                Job.job_type == "report_context_extract",
                Job.status.in_(["queued", "processing"]),
            )
            .order_by(Job.created_at.asc())
        )
        jobs = list((await self.session.scalars(stmt)).all())
        pending_jobs: list[dict[str, Any]] = []
        for job in jobs:
            payload = json.loads(job.payload_json)
            session_id = payload.get("sessionId")
            doctor_id = payload.get("doctorId")
            upload_ids = payload.get("uploadIds")
            if (
                not isinstance(session_id, str)
                or not isinstance(doctor_id, str)
                or not isinstance(upload_ids, list)
            ):
                continue
            pending_jobs.append(
                {
                    "id": encode_public_id("report_job", job.id),
                    "sessionId": session_id,
                    "doctorId": doctor_id,
                    "uploadIds": [str(upload_id) for upload_id in upload_ids],
                    "status": str(
                        payload.get("status") or _public_job_status(job.status)
                    ),
                }
            )
        return pending_jobs

    async def update_report_context_job_state(
        self,
        job_id: str,
        payload_updates: dict[str, Any],
    ) -> dict | None:
        try:
            job_pk = _decode_report_job_id(job_id)
        except ValueError:
            return None
        job = await self.session.get(Job, job_pk)
        if (
            job is None
            or job.product != "transcribe"
            or job.job_type != "report_context_extract"
        ):
            return None
        payload = json.loads(job.payload_json)
        payload.update(payload_updates)
        payload["updatedAt"] = (
            payload_updates.get("updatedAt") or datetime.now(UTC).isoformat()
        )
        job.payload_json = json.dumps(payload)
        status = str(payload_updates.get("status") or payload.get("status") or "")
        if status == "ready":
            job.status = "completed"
        elif status == "canceled":
            job.status = "cancelled"
        elif status in {"failed", "processing", "queued"}:
            job.status = "failed" if status == "failed" else status
        await self.session.commit()
        await self.session.refresh(job)
        return await self._report_context_job_payload(job)

    async def cancel_report_context_job(
        self, session_id: str, doctor_id: str, job_id: str
    ) -> dict | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        job = await self._load_report_context_job(session_model.id, job_id)
        if job is None:
            return None
        payload = json.loads(job.payload_json)
        payload["status"] = "canceled"
        payload["updatedAt"] = datetime.now(UTC).isoformat()
        payload["finishedAt"] = payload["updatedAt"]
        job.status = "cancelled"
        job.payload_json = json.dumps(payload)
        upload_ids = [
            _decode_report_upload_id(str(upload_id))
            for upload_id in payload.get("uploadIds", [])
        ]
        if upload_ids:
            stmt = select(TranscribeReportContextDocument).where(
                TranscribeReportContextDocument.session_id == session_model.id,
                TranscribeReportContextDocument.id.in_(upload_ids),
            )
            docs = (await self.session.scalars(stmt)).all()
            for doc in docs:
                doc.status = "uploaded"
        session_model.report_context_state_json = json.dumps(
            _normalize_report_context_state(
                {
                    "status": "ready",
                    "selectedChunkIds": [],
                    "appliedChunkIds": [],
                    "extractedUploadIds": _parse_report_context_state(
                        session_model.report_context_state_json
                    ).get("extractedUploadIds", []),
                    "updatedAt": payload["updatedAt"],
                }
            )
        )
        await self.session.commit()
        return await self._report_context_job_payload(job)

    async def _load_session(
        self, session_id: str, doctor_id: str
    ) -> TranscribeSession | None:
        try:
            session_pk = _decode_session_id(session_id)
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        stmt = select(TranscribeSession).where(
            TranscribeSession.id == session_pk,
            TranscribeSession.doctor_id == doctor_pk,
        )
        return await self.session.scalar(stmt)

    async def _load_report_context_upload_row(
        self,
        session_id: str,
        doctor_id: str,
        upload_id: str,
    ) -> tuple[TranscribeReportContextDocument, StoredFile] | None:
        session_model = await self._load_session(session_id, doctor_id)
        if session_model is None:
            return None
        try:
            upload_pk = _decode_report_upload_id(upload_id)
        except ValueError:
            return None
        stmt = (
            select(TranscribeReportContextDocument, StoredFile)
            .join(
                StoredFile,
                StoredFile.id == TranscribeReportContextDocument.stored_file_id,
            )
            .where(
                TranscribeReportContextDocument.id == upload_pk,
                TranscribeReportContextDocument.session_id == session_model.id,
            )
        )
        row = (await self.session.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    async def _load_report_context_documents(
        self,
        session_pk: int,
        upload_ids: list[str],
    ) -> list[TranscribeReportContextDocument]:
        if not upload_ids:
            return []
        try:
            upload_pks = [
                _decode_report_upload_id(upload_id) for upload_id in upload_ids
            ]
        except ValueError:
            return []
        stmt = (
            select(TranscribeReportContextDocument)
            .where(
                TranscribeReportContextDocument.session_id == session_pk,
                TranscribeReportContextDocument.id.in_(upload_pks),
            )
            .order_by(TranscribeReportContextDocument.created_at.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def _load_report_context_job(
        self, session_pk: int, job_id: str
    ) -> Job | None:
        try:
            job_pk = _decode_report_job_id(job_id)
        except ValueError:
            return None
        job = await self.session.get(Job, job_pk)
        if (
            job is None
            or job.product != "transcribe"
            or job.job_type != "report_context_extract"
        ):
            return None
        payload = json.loads(job.payload_json)
        try:
            payload_session_pk = _decode_session_id(str(payload["sessionId"]))
        except (KeyError, TypeError, ValueError):
            return None
        return job if payload_session_pk == session_pk else None

    def _report_context_upload_payload(
        self,
        document: TranscribeReportContextDocument,
        stored_file: StoredFile,
    ) -> dict:
        expires_at = document.created_at + REPORT_CONTEXT_UPLOAD_TTL
        return {
            "id": encode_public_id("report_upload", document.id),
            "fileName": stored_file.original_filename,
            "mimeType": stored_file.content_type,
            "sizeBytes": stored_file.size_bytes,
            "expiresAt": _serialize_datetime_string(expires_at),
        }

    async def _report_context_job_payload(self, job: Job) -> dict:
        payload = json.loads(job.payload_json)
        public_status = str(payload.get("status") or _public_job_status(job.status))
        normalized_chunks = []
        for chunk_payload in payload.get("chunks") or []:
            if not isinstance(chunk_payload, dict):
                continue
            normalized_chunk = {
                key: value for key, value in chunk_payload.items() if value is not None
            }
            extracted_at = _coerce_iso_datetime_string(chunk_payload.get("extractedAt"))
            if extracted_at is not None:
                normalized_chunk["extractedAt"] = extracted_at
            normalized_chunks.append(normalized_chunk)
        progress_payload = payload.get("progress")
        normalized_progress = None
        if isinstance(progress_payload, dict):
            normalized_progress = {
                key: value
                for key, value in progress_payload.items()
                if key not in {"startedAt", "updatedAt"} and value is not None
            }
            started_at = _coerce_iso_datetime_string(progress_payload.get("startedAt"))
            updated_at = _coerce_iso_datetime_string(progress_payload.get("updatedAt"))
            if started_at is not None:
                normalized_progress["startedAt"] = started_at
            if updated_at is not None:
                normalized_progress["updatedAt"] = updated_at

        created_at = _coerce_iso_datetime_string(payload.get("createdAt"))
        updated_at = _coerce_iso_datetime_string(payload.get("updatedAt"))
        started_at = _coerce_iso_datetime_string(payload.get("startedAt"))
        finished_at = _coerce_iso_datetime_string(payload.get("finishedAt"))

        return {
            "id": encode_public_id("report_job", job.id),
            "sessionId": str(payload["sessionId"]),
            "status": public_status,
            "uploadIds": [str(upload_id) for upload_id in payload.get("uploadIds", [])],
            "totalUnits": int(payload.get("totalUnits") or 0),
            "completedUnits": int(payload.get("completedUnits") or 0),
            "failedUnits": int(payload.get("failedUnits") or 0),
            "chunks": normalized_chunks,
            **({"error": payload["error"]} if payload.get("error") else {}),
            "createdAt": created_at or _serialize_datetime_string(job.created_at),
            **({"startedAt": started_at} if started_at is not None else {}),
            "updatedAt": updated_at or _serialize_datetime_string(job.created_at),
            **({"finishedAt": finished_at} if finished_at is not None else {}),
            **({"progress": normalized_progress} if normalized_progress else {}),
        }


class SqlQueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_default_intake_link(
        self, doctor_id: str, doctor_name: str
    ) -> IntakeLink | None:
        try:
            doctor_pk = _decode_doctor_id(doctor_id)
        except ValueError:
            return None
        stmt = (
            select(TranscribeIntakeLink)
            .where(TranscribeIntakeLink.doctor_id == doctor_pk)
            .limit(1)
        )
        existing = await self.session.scalar(stmt)
        if existing is not None:
            if existing.label != "Clinic Queue":
                existing.label = "Clinic Queue"
            await self.session.commit()
            return _intake_link_from_model(existing, doctor_name)
        model = TranscribeIntakeLink(
            doctor_id=doctor_pk,
            label="Clinic Queue",
            token=secrets.token_urlsafe(18),
            is_active=True,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return _intake_link_from_model(model, doctor_name)

    async def get_public_intake_link_by_token(self, token: str) -> IntakeLink | None:
        stmt = (
            select(TranscribeIntakeLink, User)
            .join(User, User.id == TranscribeIntakeLink.doctor_id)
            .where(
                TranscribeIntakeLink.token == token,
                TranscribeIntakeLink.is_active.is_(True),
            )
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        intake_link, doctor = row
        return _intake_link_from_model(intake_link, doctor.name)

    async def create_queue_entry(
        self,
        *,
        doctor_id: str,
        intake_link_id: str,
        intake_label: str,
        patient_name: str,
        patient_phone_number: str,
        patient_age: int | None,
        source: str,
    ) -> QueueEntry:
        model = TranscribeQueueEntry(
            doctor_id=_decode_doctor_id(doctor_id),
            intake_link_id=_decode_intake_link_id(intake_link_id),
            intake_label=intake_label,
            patient_name=patient_name,
            patient_phone_number=patient_phone_number,
            patient_age=patient_age,
            status="waiting",
            source=source,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return _queue_entry_from_model(model)

    async def find_recent_waiting_entry_by_phone(
        self, doctor_id: str, phone_number: str
    ) -> QueueEntry | None:
        stmt = (
            select(TranscribeQueueEntry)
            .where(
                TranscribeQueueEntry.doctor_id == _decode_doctor_id(doctor_id),
                TranscribeQueueEntry.patient_phone_number == phone_number,
                TranscribeQueueEntry.status == "waiting",
            )
            .order_by(TranscribeQueueEntry.submitted_at.desc())
            .limit(1)
        )
        model = await self.session.scalar(stmt)
        return _queue_entry_from_model(model) if model is not None else None

    async def list_queue_entries(self, doctor_id: str) -> list[QueueEntry]:
        stmt = (
            select(TranscribeQueueEntry)
            .where(
                TranscribeQueueEntry.doctor_id == _decode_doctor_id(doctor_id),
                TranscribeQueueEntry.status == "waiting",
            )
            .order_by(TranscribeQueueEntry.submitted_at.asc())
        )
        rows = (await self.session.scalars(stmt)).all()
        return [_queue_entry_from_model(row) for row in rows]

    async def list_queue_entries_admin(
        self,
        *,
        doctor_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list[QueueEntry]:
        stmt = select(TranscribeQueueEntry)
        if doctor_id is not None:
            stmt = stmt.where(
                TranscribeQueueEntry.doctor_id == _decode_doctor_id(doctor_id)
            )
        if status is not None:
            stmt = stmt.where(TranscribeQueueEntry.status == status)
        normalized = (search or "").strip()
        if normalized:
            like = f"%{normalized.lower()}%"
            stmt = stmt.where(
                or_(
                    TranscribeQueueEntry.patient_name.ilike(like),
                    TranscribeQueueEntry.patient_phone_number.ilike(like),
                )
            )
        stmt = stmt.order_by(TranscribeQueueEntry.created_at.desc())
        rows = (await self.session.scalars(stmt)).all()
        return [_queue_entry_from_model(row) for row in rows]

    async def get_queue_entry_by_id(self, queue_entry_id: str) -> QueueEntry | None:
        try:
            queue_entry_pk = _decode_queue_entry_id(queue_entry_id)
        except ValueError:
            return None
        model = await self.session.get(TranscribeQueueEntry, queue_entry_pk)
        return _queue_entry_from_model(model) if model is not None else None

    async def list_intake_links(self, doctor_id: str | None = None) -> list[IntakeLink]:
        stmt = select(TranscribeIntakeLink, User).join(
            User,
            User.id == TranscribeIntakeLink.doctor_id,
        )
        if doctor_id is not None:
            stmt = stmt.where(
                TranscribeIntakeLink.doctor_id == _decode_doctor_id(doctor_id)
            )
        stmt = stmt.order_by(TranscribeIntakeLink.updated_at.desc())
        rows = (await self.session.execute(stmt)).all()
        return [_intake_link_from_model(link, doctor.name) for link, doctor in rows]

    async def get_intake_link_by_id(self, intake_link_id: str) -> IntakeLink | None:
        try:
            intake_link_pk = _decode_intake_link_id(intake_link_id)
        except ValueError:
            return None
        stmt = (
            select(TranscribeIntakeLink, User)
            .join(User, User.id == TranscribeIntakeLink.doctor_id)
            .where(TranscribeIntakeLink.id == intake_link_pk)
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        link, doctor = row
        return _intake_link_from_model(link, doctor.name)

    async def update_intake_link(
        self,
        intake_link_id: str,
        *,
        label: str | None,
        is_active: bool | None,
    ) -> IntakeLink | None:
        try:
            intake_link_pk = _decode_intake_link_id(intake_link_id)
        except ValueError:
            return None
        model = await self.session.get(TranscribeIntakeLink, intake_link_pk)
        if model is None:
            return None
        if label is not None:
            model.label = label
        if is_active is not None:
            model.is_active = is_active
        model.updated_at = datetime.now(UTC)
        await self.session.commit()
        doctor = await self.session.get(User, model.doctor_id)
        if doctor is None:
            return None
        return _intake_link_from_model(model, doctor.name)

    async def accept_queue_entry(
        self, queue_entry_id: str, session_id: str
    ) -> QueueEntry | None:
        try:
            queue_entry_pk = _decode_queue_entry_id(queue_entry_id)
        except ValueError:
            return None
        model = await self.session.get(TranscribeQueueEntry, queue_entry_pk)
        if model is None:
            return None
        if model.status != "waiting":
            return _queue_entry_from_model(model)
        accepted_at = datetime.now(UTC)
        model.status = "accepted"
        model.accepted_at = accepted_at
        model.updated_at = accepted_at
        model.session_id = _decode_session_id(session_id)
        await self.session.commit()
        await self.session.refresh(model)
        return _queue_entry_from_model(model)

    async def update_queue_entry_status(
        self, queue_entry_id: str, status: str
    ) -> QueueEntry | None:
        try:
            queue_entry_pk = _decode_queue_entry_id(queue_entry_id)
        except ValueError:
            return None
        model = await self.session.get(TranscribeQueueEntry, queue_entry_pk)
        if model is None or model.status != "waiting":
            return None
        model.status = status
        model.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(model)
        return _queue_entry_from_model(model)
