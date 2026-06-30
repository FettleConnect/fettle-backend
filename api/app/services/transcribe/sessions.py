from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.config import get_settings
from api.app.db.transcribe import SessionFactory
from api.app.schemas.hospital.transcribe import (
    ConsultationSession,
    CreateSessionRequest,
    DraftConsultationDocument,
    ErrorResponse,
    PatientProfile,
    ReportContextExtractionJob,
    ReportContextUpload,
)
from api.app.services.transcribe.audio_metadata import canonicalize_audio_upload
from api.app.services.transcribe.fettle_sync import FettleSyncService
from api.app.services.transcribe.pdf import (
    build_document_artifact,
    build_transcript_pdf,
)
from api.app.services.transcribe.persistence import (
    SqlSessionRepository,
    SqlSyncRepository,
)
from api.app.services.transcribe.storage import LocalStorage
from api.app.services.transcribe.transcription_provider import (
    LocalTranscriptionProvider,
)

ALLOWED_REPORT_CONTEXT_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class SessionService:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.transcription_provider = LocalTranscriptionProvider()

    async def create_session(
        self, doctor_id: str, request: CreateSessionRequest
    ) -> ConsultationSession:
        return await self.repository.create_session(
            doctor_id=doctor_id,
            patient_name=request.patientName,
            patient_phone_number=request.patientPhoneNumber,
            patient_age=request.patientAge,
        )

    async def list_sessions(
        self, doctor_id: str, search: str | None = None
    ) -> list[ConsultationSession]:
        return await self.repository.list_sessions(doctor_id, search)

    async def list_sessions_admin(
        self, doctor_id: str | None = None, search: str | None = None
    ) -> list[ConsultationSession]:
        return await self.repository.list_sessions_admin(
            doctor_id=doctor_id, search=search
        )

    async def get_session(self, session_id: str, doctor_id: str) -> ConsultationSession:
        session = await self.repository.get_session(session_id, doctor_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        return session

    async def get_session_any(self, session_id: str) -> ConsultationSession:
        session = await self.repository.get_session_any(session_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        return session

    async def get_patient_history(
        self, session_id: str, doctor_id: str
    ) -> list[ConsultationSession]:
        session = await self.repository.get_session(session_id, doctor_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        return await self.repository.get_patient_history(session_id, doctor_id)

    async def get_patient_profile_by_phone(
        self, phone_number: str, doctor_id: str
    ) -> PatientProfile | None:
        return await self.repository.get_patient_profile_by_phone(
            phone_number, doctor_id
        )

    async def update_case_status(
        self, session_id: str, doctor_id: str, case_status: str
    ) -> ConsultationSession:
        session = await self.repository.update_case_status(
            session_id, doctor_id, case_status
        )
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        return session

    async def upload_audio(
        self,
        session_id: str,
        doctor_id: str,
        *,
        file_name: str | None,
        mime_type: str | None,
        duration_seconds: float | None,
        data: bytes,
        storage,
    ) -> ConsultationSession:
        session = await self.repository.get_session(session_id, doctor_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        if not data:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    message="Audio upload payload cannot be empty."
                ).model_dump(),
            )
        metadata = canonicalize_audio_upload(
            session_id, file_name, mime_type, duration_seconds, data
        )
        normalized_duration_seconds = cast(
            float | int | str, metadata["durationSeconds"]
        )
        storage_key = storage.save_bytes(
            f"transcribe/sessions/{session_id}/audio", str(metadata["fileName"]), data
        )
        updated = await self.repository.store_audio(
            session_id,
            doctor_id,
            file_name=str(metadata["fileName"]),
            mime_type=str(metadata["mimeType"]),
            duration_seconds=float(normalized_duration_seconds),
            size_bytes=len(data),
            storage_key=storage_key,
        )
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        await self._enqueue_fettle_sync(updated, "draft_updated")
        return updated

    async def request_transcription(
        self, session_id: str, doctor_id: str, idempotency_key: str | None
    ) -> ConsultationSession:
        session = await self.repository.get_session(session_id, doctor_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        if session.audioArtifact is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Upload audio before requesting transcription."
                ).model_dump(),
            )
        updated = await self.repository.mark_transcription_processing(
            session_id,
            doctor_id,
            provider_name=self.transcription_provider.provider_name,
            idempotency_key=idempotency_key,
        )
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        self._launch_background(self._complete_transcription(session_id))
        return updated

    async def request_transcription_admin(
        self, session_id: str, idempotency_key: str | None
    ) -> ConsultationSession:
        session = await self.get_session_any(session_id)
        return await self.request_transcription(
            session_id, session.doctorId, idempotency_key
        )

    async def update_draft_document(
        self,
        session_id: str,
        doctor_id: str,
        draft_document: DraftConsultationDocument,
    ) -> ConsultationSession:
        updated = await self.repository.update_draft_document(
            session_id,
            doctor_id,
            draft_document.model_dump(),
        )
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        return updated

    async def generate_session_pdf(
        self, session_id: str, doctor_id: str, storage: LocalStorage
    ) -> ConsultationSession:
        session = await self.repository.get_session(session_id, doctor_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        if not session.transcription or not session.transcription.draftDocument:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Run transcription before generating the consultation PDF."
                ).model_dump(),
            )
        artifact, pdf_bytes = build_document_artifact(
            session, session.transcription.draftDocument
        )
        storage_key = storage.save_bytes(
            f"transcribe/sessions/{session_id}/artifacts", artifact.fileName, pdf_bytes
        )
        updated = await self.repository.store_generated_pdf(
            session_id,
            doctor_id,
            file_name=artifact.fileName,
            storage_key=storage_key,
            size_bytes=artifact.sizeBytes,
        )
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        return updated

    async def generate_session_pdf_admin(
        self, session_id: str, storage: LocalStorage
    ) -> ConsultationSession:
        session = await self.get_session_any(session_id)
        return await self.generate_session_pdf(session_id, session.doctorId, storage)

    async def get_transcript_download(
        self, session_id: str, doctor_id: str
    ) -> tuple[str, str, bytes]:
        session = await self.repository.get_session(session_id, doctor_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        if not session.transcription or session.transcription.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Generate a transcription before downloading the transcript."
                ).model_dump(),
            )
        return (
            f"{session_id}-transcript.pdf",
            "application/pdf",
            build_transcript_pdf(session),
        )

    async def get_transcript_download_admin(
        self, session_id: str
    ) -> tuple[str, str, bytes]:
        session = await self.get_session_any(session_id)
        return await self.get_transcript_download(session_id, session.doctorId)

    async def get_audio_download(
        self, session_id: str, doctor_id: str, storage: LocalStorage
    ) -> tuple[str, str, bytes]:
        row = await self.repository.get_audio_download(session_id, doctor_id)
        if row is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Upload consultation audio before downloading it."
                ).model_dump(),
            )
        file_model, _audio = row
        return (
            file_model.original_filename,
            file_model.content_type,
            storage.read_bytes(file_model.storage_key),
        )

    async def get_audio_download_admin(
        self, session_id: str, storage: LocalStorage
    ) -> tuple[str, str, bytes]:
        row = await self.repository.get_audio_download_any(session_id)
        if row is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Upload consultation audio before downloading it."
                ).model_dump(),
            )
        file_model, _audio = row
        return (
            file_model.original_filename,
            file_model.content_type,
            storage.read_bytes(file_model.storage_key),
        )

    async def get_pdf_download(
        self, session_id: str, doctor_id: str, storage: LocalStorage
    ) -> tuple[str, str, bytes]:
        file_model = await self.repository.get_pdf_download(session_id, doctor_id)
        if file_model is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Generate the consultation PDF before downloading it."
                ).model_dump(),
            )
        return (
            file_model.original_filename,
            file_model.content_type,
            storage.read_bytes(file_model.storage_key),
        )

    async def get_pdf_download_admin(
        self, session_id: str, storage: LocalStorage
    ) -> tuple[str, str, bytes]:
        file_model = await self.repository.get_pdf_download_any(session_id)
        if file_model is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Generate the consultation PDF before downloading it."
                ).model_dump(),
            )
        return (
            file_model.original_filename,
            file_model.content_type,
            storage.read_bytes(file_model.storage_key),
        )

    async def upload_report_context_file(
        self,
        session_id: str,
        doctor_id: str,
        *,
        file_name: str | None,
        mime_type: str | None,
        data: bytes,
        storage: LocalStorage,
    ) -> ReportContextUpload:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        normalized_mime_type = (mime_type or "").split(";")[0].strip().lower()
        if normalized_mime_type not in ALLOWED_REPORT_CONTEXT_MIME_TYPES:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    message="Report uploads must be PDF, JPEG, PNG, or WebP."
                ).model_dump(),
            )
        if not data:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    message="Report upload payload cannot be empty."
                ).model_dump(),
            )
        normalized_file_name = (
            file_name or f"report-context{Path(file_name or '').suffix or '.bin'}"
        )
        storage_key = storage.save_bytes(
            f"transcribe/sessions/{session_id}/report-context",
            normalized_file_name,
            data,
        )
        upload = await self.repository.store_report_context_upload(
            session_id,
            doctor_id,
            file_name=normalized_file_name,
            mime_type=normalized_mime_type,
            size_bytes=len(data),
            storage_key=storage_key,
        )
        if upload is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Session not found.").model_dump(),
            )
        return ReportContextUpload.model_validate(upload)

    async def list_report_context_uploads(
        self, session_id: str, doctor_id: str
    ) -> list[ReportContextUpload]:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        uploads = await self.repository.list_report_context_uploads(
            session_id, doctor_id
        )
        return [ReportContextUpload.model_validate(upload) for upload in uploads]

    async def delete_report_context_upload(
        self, session_id: str, upload_id: str, doctor_id: str
    ) -> None:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        deleted = await self.repository.delete_report_context_upload(
            session_id, upload_id, doctor_id
        )
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Report upload not found.").model_dump(),
            )

    async def get_report_context_upload_content(
        self,
        session_id: str,
        upload_id: str,
        doctor_id: str,
        storage: LocalStorage,
    ) -> tuple[str, str, bytes]:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        file_model = await self.repository.get_report_context_upload_content(
            session_id, upload_id, doctor_id
        )
        if file_model is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Report upload not found.").model_dump(),
            )
        return (
            file_model.original_filename,
            file_model.content_type,
            storage.read_bytes(file_model.storage_key),
        )

    async def extract_report_context(
        self,
        session_id: str,
        upload_ids: list[str],
        doctor_id: str,
    ) -> ConsultationSession:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        if not upload_ids:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    message="At least one report upload must be selected."
                ).model_dump(),
            )
        updated = await self.repository.extract_report_context(
            session_id, doctor_id, upload_ids
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="One or more report uploads could not be loaded."
                ).model_dump(),
            )
        return updated

    async def start_report_context_extraction_job(
        self,
        session_id: str,
        upload_ids: list[str],
        doctor_id: str,
    ) -> ReportContextExtractionJob:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        if not upload_ids:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    message="At least one report upload must be selected."
                ).model_dump(),
            )
        job = await self.repository.create_report_context_job(
            session_id, doctor_id, upload_ids
        )
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="One or more report uploads could not be loaded."
                ).model_dump(),
            )
        self._launch_background(self._complete_report_context_job(str(job["id"])))
        return ReportContextExtractionJob.model_validate(job)

    async def get_report_context_extraction_job(
        self,
        session_id: str,
        job_id: str,
        doctor_id: str,
    ) -> ReportContextExtractionJob:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        job = await self.repository.get_report_context_job(
            session_id, doctor_id, job_id
        )
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="Report-context extraction job not found."
                ).model_dump(),
            )
        return ReportContextExtractionJob.model_validate(job)

    async def get_active_report_context_extraction_job(
        self,
        session_id: str,
        doctor_id: str,
    ) -> ReportContextExtractionJob | None:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        job = await self.repository.get_active_report_context_job(session_id, doctor_id)
        if job is None:
            return None
        return ReportContextExtractionJob.model_validate(job)

    async def cancel_report_context_extraction_job(
        self,
        session_id: str,
        job_id: str,
        doctor_id: str,
    ) -> ReportContextExtractionJob:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        job = await self.repository.cancel_report_context_job(
            session_id, doctor_id, job_id
        )
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="Report-context extraction job not found."
                ).model_dump(),
            )
        return ReportContextExtractionJob.model_validate(job)

    async def suggest_report_context_draft(
        self,
        session_id: str,
        chunk_ids: list[str],
        custom_chunks: list[str],
        doctor_id: str,
    ) -> dict:
        session = await self.get_session(session_id, doctor_id)
        if session.reportContext is None or session.reportContext.status != "ready":
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Extract report context before requesting draft suggestions."
                ).model_dump(),
            )
        selected = [
            chunk
            for chunk in session.reportContext.chunks
            if not chunk_ids or chunk.id in chunk_ids
        ]
        combined = "\n".join([chunk.text for chunk in selected] + custom_chunks)
        draft = {
            "title": "Report Context Suggestion",
            "clinicalSummary": combined or "No report context selected.",
            "medications": [],
            "testsPrescribed": [],
            "nextSteps": ["Review imported report context before finalizing."],
            "cautions": [],
        }
        return {
            "draftDocument": draft,
            "chunkIds": [chunk.id for chunk in selected],
            "customChunks": custom_chunks,
        }

    async def apply_report_context_draft(
        self,
        session_id: str,
        chunk_ids: list[str],
        custom_chunks: list[str],
        draft_document: DraftConsultationDocument,
        doctor_id: str,
    ) -> ConsultationSession:
        _ = await self.suggest_report_context_draft(
            session_id, chunk_ids, custom_chunks, doctor_id
        )
        return await self.update_draft_document(session_id, doctor_id, draft_document)

    async def _complete_transcription(self, session_id: str) -> None:
        async with SessionFactory() as session:
            repository = SqlSessionRepository(session)
            session_view = await repository.get_session_any(session_id)
            if session_view is None:
                return
            result = await self.transcription_provider.transcribe(session_view)
            updated = await repository.complete_transcription(session_id, result)
            if updated is not None:
                await self._enqueue_fettle_sync(
                    updated, "transcription_completed", db_session=session
                )

    async def _complete_report_context_job(self, job_id: str) -> None:
        async with SessionFactory() as session:
            repository = SqlSessionRepository(session)
            await repository.complete_report_context_job(job_id)

    async def _enqueue_fettle_sync(
        self,
        session: ConsultationSession,
        trigger: str,
        db_session: AsyncSession | None = None,
    ) -> None:
        active_session = db_session or getattr(self.repository, "session", None)
        if active_session is None:
            return
        await FettleSyncService(
            SqlSyncRepository(active_session), get_settings()
        ).enqueue_session_sync(session, trigger)

    def _ensure_report_context_ready_for_uploads(
        self, session: ConsultationSession
    ) -> None:
        if session.transcription is None or session.transcription.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Complete transcription before extracting report context."
                ).model_dump(),
            )

    def _launch_background(self, coroutine) -> None:
        thread = threading.Thread(target=lambda: asyncio.run(coroutine), daemon=True)
        thread.start()
