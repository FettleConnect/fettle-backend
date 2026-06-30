from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.config import get_settings
from api.app.transcribe.db import SessionFactory
from api.app.transcribe.repositories.sqlalchemy import (
    SqlSessionRepository,
    SqlSyncRepository,
)
from api.app.transcribe.schemas.common import (
    ConsultationSession,
    ErrorResponse,
    ReportContextChunk,
)
from api.app.transcribe.schemas.queue import PatientProfile
from api.app.transcribe.schemas.sessions import (
    CreateSessionRequest,
    DraftConsultationDocument,
    ReportContextExtractionJob,
    ReportContextUpload,
)
from api.app.transcribe.services.audio_metadata import canonicalize_audio_upload
from api.app.transcribe.services.fettle_sync import FettleSyncService
from api.app.transcribe.services.pdf import (
    build_document_artifact,
    build_transcript_pdf,
)
from api.app.transcribe.services.report_context_provider import (
    ReportContextPartialFailureError,
    ReportContextProviderError,
    StoredReportContextUpload,
    create_report_context_provider,
)
from api.app.transcribe.services.transcription_provider import (
    StoredAudioInput,
    TranscriptionProviderError,
    create_transcription_provider,
)
from api.app.transcribe.storage import LocalStorage

ALLOWED_REPORT_CONTEXT_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class SessionService:
    def __init__(self, repository, settings=None) -> None:
        self.repository = repository
        self.settings = settings or get_settings()
        self.transcription_provider = create_transcription_provider(self.settings)
        self.report_context_provider = create_report_context_provider(self.settings)

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
        return await self._run_report_context_extraction(
            session=session,
            session_id=session_id,
            upload_ids=upload_ids,
            doctor_id=doctor_id,
            job_id=None,
        )

    async def start_report_context_extraction_job(
        self,
        session_id: str,
        upload_ids: list[str],
        doctor_id: str,
    ) -> ReportContextExtractionJob:
        session = await self.get_session(session_id, doctor_id)
        self._ensure_report_context_ready_for_uploads(session)
        normalized_upload_ids = self._normalize_report_upload_ids(session, upload_ids)
        job = await self.repository.create_report_context_job(
            session_id, doctor_id, normalized_upload_ids
        )
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="One or more report uploads could not be loaded."
                ).model_dump(),
            )
        self._launch_background(
            self._complete_report_context_job(
                session_id=session_id,
                upload_ids=normalized_upload_ids,
                doctor_id=doctor_id,
                job_id=str(job["id"]),
            )
        )
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

    async def resume_pending_report_context_jobs(self) -> int:
        pending_jobs = await self.repository.list_pending_report_context_jobs()
        resumed = 0
        for job in pending_jobs:
            job_id = str(job["id"])
            session_id = str(job["sessionId"])
            doctor_id = str(job["doctorId"])
            upload_ids = [str(upload_id) for upload_id in job.get("uploadIds", [])]
            if not upload_ids:
                await self.repository.update_report_context_job_state(
                    job_id,
                    {
                        "status": "failed",
                        "error": "Report-context extraction job had no upload ids to process.",
                        "finishedAt": datetime.now(UTC).isoformat(),
                    },
                )
                continue
            session = await self.repository.get_session(session_id, doctor_id)
            if session is None:
                await self.repository.update_report_context_job_state(
                    job_id,
                    {
                        "status": "failed",
                        "error": "Session for report-context extraction job no longer exists.",
                        "finishedAt": datetime.now(UTC).isoformat(),
                    },
                )
                continue
            extracted_upload_ids = (
                session.reportContext.extractedUploadIds if session.reportContext else []
            )
            if (
                session.reportContext is not None
                and session.reportContext.status == "ready"
                and all(upload_id in extracted_upload_ids for upload_id in upload_ids)
            ):
                await self.repository.update_report_context_job_state(
                    job_id,
                    {
                        "status": "ready",
                        "totalUnits": (
                            session.reportContext.progress.totalUnits
                            if session.reportContext.progress is not None
                            else len(session.reportContext.chunks)
                        ),
                        "completedUnits": (
                            session.reportContext.progress.completedUnits
                            if session.reportContext.progress is not None
                            else len(session.reportContext.chunks)
                        ),
                        "failedUnits": (
                            session.reportContext.progress.failedUnits
                            if session.reportContext.progress is not None
                            else 0
                        ),
                        "chunks": self._serialize_report_context_chunks(
                            session.reportContext.chunks,
                            session.reportContext.extractedUploadIds,
                        ),
                        "progress": (
                            session.reportContext.progress.model_dump(exclude_none=True)
                            if session.reportContext.progress is not None
                            else None
                        ),
                        "finishedAt": datetime.now(UTC).isoformat(),
                        "error": session.reportContext.error,
                    },
                )
                continue
            self._launch_background(
                self._complete_report_context_job(
                    session_id=session_id,
                    upload_ids=upload_ids,
                    doctor_id=doctor_id,
                    job_id=job_id,
                )
            )
            resumed += 1
        return resumed

    async def suggest_report_context_draft(
        self,
        session_id: str,
        chunk_ids: list[str],
        custom_chunks: list[dict],
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
        if session.transcription is None or session.transcription.draftDocument is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Run transcription before applying report context."
                ).model_dump(),
            )
        selected = self._select_report_context_chunks(
            session, chunk_ids, custom_chunks, reject_applied=True
        )
        try:
            return await self.report_context_provider.suggest_draft(
                session_id,
                DraftConsultationDocument.model_validate(
                    session.transcription.draftDocument.model_dump(exclude_none=True)
                ),
                selected,
            )
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, (ReportContextProviderError, RuntimeError))
                else "Report context suggestion failed unexpectedly."
            )
            raise HTTPException(
                status_code=502,
                detail=ErrorResponse(message=message).model_dump(),
            ) from exc

    async def apply_report_context_draft(
        self,
        session_id: str,
        chunk_ids: list[str],
        custom_chunks: list[dict],
        draft_document: DraftConsultationDocument,
        doctor_id: str,
    ) -> ConsultationSession:
        session = await self.get_session(session_id, doctor_id)
        if session.reportContext is None or session.reportContext.status != "ready":
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Complete report extraction successfully before applying report context."
                ).model_dump(),
            )
        selected = self._select_report_context_chunks(
            session, chunk_ids, custom_chunks, reject_applied=True
        )
        updated = await self.update_draft_document(session_id, doctor_id, draft_document)
        next_state = {
            "status": "ready",
            "selectedChunkIds": [chunk.id for chunk in selected],
            "appliedChunkIds": sorted(
                {
                    *(session.reportContext.appliedChunkIds if session.reportContext else []),
                    *[chunk.id for chunk in selected],
                }
            ),
            "extractedUploadIds": session.reportContext.extractedUploadIds,
            "progress": (
                session.reportContext.progress.model_dump(exclude_none=True)
                if session.reportContext and session.reportContext.progress is not None
                else None
            ),
            "updatedAt": datetime.now(UTC).isoformat(),
            "error": session.reportContext.error if session.reportContext else None,
        }
        final_session = await self.repository.update_report_context_state(
            session_id, doctor_id, next_state
        )
        return final_session or updated

    async def _run_report_context_extraction(
        self,
        *,
        session: ConsultationSession,
        session_id: str,
        upload_ids: list[str],
        doctor_id: str,
        job_id: str | None,
    ) -> ConsultationSession:
        normalized_upload_ids = self._normalize_report_upload_ids(session, upload_ids)
        upload_rows = await self.repository.get_report_context_upload_blobs(
            session_id, doctor_id, normalized_upload_ids
        )
        if upload_rows is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="One or more report uploads could not be loaded."
                ).model_dump(),
            )
        storage = LocalStorage()
        uploads = [
            StoredReportContextUpload(
                id=str(row["id"]),
                file_name=str(row["fileName"]),
                mime_type=str(row["mimeType"]),
                size_bytes=int(row["sizeBytes"]),
                data=storage.read_bytes(str(row["storageKey"])),
            )
            for row in upload_rows
        ]
        now = datetime.now(UTC).isoformat()
        total_units = self._estimate_report_context_units(uploads)
        base_progress = {
            "totalUnits": total_units,
            "completedUnits": 0,
            "failedUnits": 0,
            "startedAt": now,
            "updatedAt": now,
            "etaSecondsLower": total_units * 4 if total_units else 0,
            "etaSecondsUpper": total_units * 18 if total_units else 0,
            "currentStage": "Preparing report pages",
        }
        processing_state = {
            "status": "processing",
            "selectedChunkIds": [],
            "appliedChunkIds": (
                session.reportContext.appliedChunkIds if session.reportContext else []
            ),
            "extractedUploadIds": (
                session.reportContext.extractedUploadIds if session.reportContext else []
            ),
            "progress": base_progress,
            "updatedAt": now,
            "error": None,
        }
        updated_processing = await self.repository.update_report_context_state(
            session_id, doctor_id, processing_state
        )
        if updated_processing is not None:
            session = updated_processing
        if job_id is not None:
            await self.repository.update_report_context_job_state(
                job_id,
                {
                    "status": "processing",
                    "totalUnits": total_units,
                    "completedUnits": 0,
                    "failedUnits": 0,
                    "progress": base_progress,
                    "startedAt": now,
                },
            )

        progress_chunks: list[dict] = []

        async def on_progress(progress) -> None:
            progress_state = {
                "totalUnits": progress.total_units,
                "completedUnits": progress.completed_units,
                "failedUnits": progress.failed_units,
                "startedAt": now,
                "updatedAt": datetime.now(UTC).isoformat(),
                "etaSecondsLower": 0,
                "etaSecondsUpper": 0,
                "currentStage": (
                    "Finalizing results"
                    if progress.completed_units + progress.failed_units >= progress.total_units
                    else "Extracting report content"
                ),
            }
            await self.repository.update_report_context_state(
                session_id,
                doctor_id,
                {
                    "status": "processing",
                    "selectedChunkIds": [],
                    "appliedChunkIds": (
                        session.reportContext.appliedChunkIds if session.reportContext else []
                    ),
                    "extractedUploadIds": (
                        session.reportContext.extractedUploadIds if session.reportContext else []
                    ),
                    "progress": progress_state,
                    "updatedAt": progress_state["updatedAt"],
                    "error": None,
                },
            )
            if job_id is not None:
                progress_chunks[:] = self._serialize_report_context_chunks(
                    progress.extracted_chunks, normalized_upload_ids
                )
                await self.repository.update_report_context_job_state(
                    job_id,
                    {
                        "status": "processing",
                        "totalUnits": progress.total_units,
                        "completedUnits": progress.completed_units,
                        "failedUnits": progress.failed_units,
                        "chunks": progress_chunks,
                        "progress": progress_state,
                    },
                )

        try:
            chunks = await self.report_context_provider.extract_chunks(
                session_id, uploads, on_progress=on_progress
            )
            ready_state = {
                "status": "ready",
                "selectedChunkIds": [],
                "appliedChunkIds": (
                    session.reportContext.appliedChunkIds if session.reportContext else []
                ),
                "extractedUploadIds": sorted(
                    {
                        *(session.reportContext.extractedUploadIds if session.reportContext else []),
                        *normalized_upload_ids,
                    }
                ),
                "progress": {
                    "totalUnits": total_units,
                    "completedUnits": total_units,
                    "failedUnits": 0,
                    "startedAt": now,
                    "updatedAt": datetime.now(UTC).isoformat(),
                    "etaSecondsLower": 0,
                    "etaSecondsUpper": 0,
                    "currentStage": "Completed",
                },
                "updatedAt": datetime.now(UTC).isoformat(),
                "error": None,
            }
            updated = await self.repository.finalize_report_context_extraction(
                session_id,
                doctor_id,
                normalized_upload_ids,
                self._serialize_report_context_chunks(chunks, normalized_upload_ids),
                ready_state,
            )
            if updated is None:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorResponse(message="Session not found.").model_dump(),
                )
            if job_id is not None:
                await self.repository.update_report_context_job_state(
                    job_id,
                    {
                        "status": "ready",
                        "totalUnits": total_units,
                        "completedUnits": total_units,
                        "failedUnits": 0,
                        "chunks": self._serialize_report_context_chunks(
                            chunks, normalized_upload_ids
                        ),
                        "progress": ready_state["progress"],
                        "finishedAt": datetime.now(UTC).isoformat(),
                    },
                )
            return updated
        except ReportContextPartialFailureError as exc:
            ready_state = {
                "status": "ready",
                "selectedChunkIds": [],
                "appliedChunkIds": (
                    session.reportContext.appliedChunkIds if session.reportContext else []
                ),
                "extractedUploadIds": sorted(
                    {
                        *(session.reportContext.extractedUploadIds if session.reportContext else []),
                        *normalized_upload_ids,
                    }
                ),
                "progress": {
                    "totalUnits": exc.total_units,
                    "completedUnits": exc.completed_units,
                    "failedUnits": exc.failed_units,
                    "startedAt": now,
                    "updatedAt": datetime.now(UTC).isoformat(),
                    "currentStage": "Completed with skipped pages",
                },
                "updatedAt": datetime.now(UTC).isoformat(),
                "error": str(exc),
            }
            updated = await self.repository.finalize_report_context_extraction(
                session_id,
                doctor_id,
                normalized_upload_ids,
                self._serialize_report_context_chunks(
                    exc.partial_chunks, normalized_upload_ids
                ),
                ready_state,
            )
            if updated is None:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorResponse(message="Session not found.").model_dump(),
                )
            if job_id is not None:
                await self.repository.update_report_context_job_state(
                    job_id,
                    {
                        "status": "ready",
                        "totalUnits": exc.total_units,
                        "completedUnits": exc.completed_units,
                        "failedUnits": exc.failed_units,
                        "chunks": self._serialize_report_context_chunks(
                            exc.partial_chunks, normalized_upload_ids
                        ),
                        "progress": ready_state["progress"],
                        "error": str(exc),
                        "finishedAt": datetime.now(UTC).isoformat(),
                    },
                )
            return updated
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, (ReportContextProviderError, RuntimeError))
                else "Report context extraction failed unexpectedly."
            )
            failed_state = {
                "status": "failed",
                "selectedChunkIds": [],
                "appliedChunkIds": (
                    session.reportContext.appliedChunkIds if session.reportContext else []
                ),
                "extractedUploadIds": (
                    session.reportContext.extractedUploadIds if session.reportContext else []
                ),
                "progress": {
                    "totalUnits": total_units,
                    "completedUnits": 0,
                    "failedUnits": total_units or len(normalized_upload_ids),
                    "startedAt": now,
                    "updatedAt": datetime.now(UTC).isoformat(),
                    "currentStage": "Failed",
                },
                "updatedAt": datetime.now(UTC).isoformat(),
                "error": message,
            }
            await self.repository.update_report_context_state(
                session_id, doctor_id, failed_state
            )
            if job_id is not None:
                await self.repository.update_report_context_job_state(
                    job_id,
                    {
                        "status": "failed",
                        "totalUnits": total_units,
                        "completedUnits": 0,
                        "failedUnits": total_units or len(normalized_upload_ids),
                        "chunks": progress_chunks,
                        "progress": failed_state["progress"],
                        "error": message,
                        "finishedAt": datetime.now(UTC).isoformat(),
                    },
                )
            raise HTTPException(
                status_code=502,
                detail=ErrorResponse(message=message).model_dump(),
            ) from exc

    def _normalize_report_upload_ids(
        self, session: ConsultationSession, upload_ids: list[str]
    ) -> list[str]:
        unique_upload_ids = [upload_id for upload_id in dict.fromkeys(upload_ids) if upload_id]
        if not unique_upload_ids:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    message="At least one report upload must be selected."
                ).model_dump(),
            )
        extracted_upload_ids = (
            session.reportContext.extractedUploadIds if session.reportContext else []
        )
        next_upload_ids = [
            upload_id
            for upload_id in unique_upload_ids
            if upload_id not in extracted_upload_ids
        ]
        if not next_upload_ids:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="No new report files to extract. Add new files before extracting again."
                ).model_dump(),
            )
        return next_upload_ids

    def _select_report_context_chunks(
        self,
        session: ConsultationSession,
        chunk_ids: list[str],
        custom_chunks: list[dict],
        *,
        reject_applied: bool,
    ):
        if session.reportContext is None or session.reportContext.status != "ready":
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Complete report extraction successfully before using report context."
                ).model_dump(),
            )
        selected = [
            chunk
            for chunk in session.reportContext.chunks
            if chunk.id in chunk_ids
        ]
        selected.extend(
            [ReportContextChunk.model_validate(chunk) for chunk in (custom_chunks or [])]
        )
        if not selected:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    message="At least one report-context chunk must be selected."
                ).model_dump(),
            )
        if reject_applied and session.reportContext is not None:
            already_applied = set(session.reportContext.appliedChunkIds)
            reused = [chunk.id for chunk in selected if chunk.id in already_applied]
            if reused:
                raise HTTPException(
                    status_code=409,
                    detail=ErrorResponse(
                        message="Some selected report-context chunks were already applied to the draft."
                    ).model_dump(),
                )
        return selected

    def _estimate_report_context_units(
        self, uploads: list[StoredReportContextUpload]
    ) -> int:
        total = 0
        for upload in uploads:
            if upload.mime_type == "application/pdf":
                try:
                    import fitz

                    document = fitz.open(stream=upload.data, filetype="pdf")
                    try:
                        total += document.page_count
                    finally:
                        document.close()
                except Exception:
                    total += 1
            else:
                total += 1
        return total

    def _serialize_report_context_chunks(
        self,
        chunks,
        upload_ids: list[str],
    ) -> list[dict]:
        upload_map = {index: upload_id for index, upload_id in enumerate(upload_ids)}
        return [
            {
                "id": chunk.id,
                "uploadId": upload_map.get(chunk.sourceIndex or 0),
                "sourceFileName": chunk.sourceFileName,
                **({"sourceIndex": chunk.sourceIndex} if chunk.sourceIndex is not None else {}),
                **({"pageNumber": chunk.pageNumber} if chunk.pageNumber is not None else {}),
                "title": chunk.title,
                "category": chunk.category,
                "text": chunk.text,
                "extractedAt": chunk.extractedAt.isoformat().replace("+00:00", "Z"),
                **({"label": chunk.label} if chunk.label else {}),
            }
            for chunk in chunks
        ]

    async def _complete_transcription(self, session_id: str) -> None:
        async with SessionFactory() as session:
            repository = SqlSessionRepository(session)
            session_view = await repository.get_session_any(session_id)
            if session_view is None:
                return
            storage = LocalStorage()
            audio_row = await repository.get_audio_download_any(session_id)
            if audio_row is None:
                await repository.fail_transcription(
                    session_id,
                    "Upload consultation audio before requesting transcription.",
                )
                return
            file_model, audio_model = audio_row
            audio = StoredAudioInput(
                file_name=file_model.original_filename,
                mime_type=file_model.content_type,
                data=storage.read_bytes(file_model.storage_key),
                duration_seconds=float(audio_model.duration_seconds or 0),
            )
            try:
                result = await self.transcription_provider.transcribe(session_view, audio)
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, (TranscriptionProviderError, RuntimeError))
                    else "Transcription failed unexpectedly."
                )
                await repository.fail_transcription(session_id, message)
                return
            updated = await repository.complete_transcription(session_id, result)
            if updated is not None:
                await self._enqueue_fettle_sync(
                    updated, "transcription_completed", db_session=session
                )

    async def _complete_report_context_job(
        self,
        *,
        session_id: str,
        upload_ids: list[str],
        doctor_id: str,
        job_id: str,
    ) -> None:
        async with SessionFactory() as session:
            repository = SqlSessionRepository(session)
            service = SessionService(repository, self.settings)
            session_view = await repository.get_session(session_id, doctor_id)
            if session_view is None:
                return
            await service._run_report_context_extraction(
                session=session_view,
                session_id=session_id,
                upload_ids=upload_ids,
                doctor_id=doctor_id,
                job_id=job_id,
            )

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
