from __future__ import annotations

from datetime import UTC, datetime

from api.app.transcribe.schemas.common import (
    ConsultationSession,
    DoctorSummary,
    PatientSummary,
    ReportContextChunk,
    SessionReportContext,
)
from api.app.transcribe.services.sessions import SessionService


def _service() -> SessionService:
    return object.__new__(SessionService)


def _session_with_report_context(*chunks: ReportContextChunk) -> ConsultationSession:
    now = datetime.now(UTC)
    return ConsultationSession(
        id="session-1",
        doctorId="doctor-1",
        patient=PatientSummary(name="Patient", phoneNumber="9999999999", age=31),
        doctor=DoctorSummary(name="Doctor", department="Medicine"),
        createdAt=now,
        updatedAt=now,
        reportContext=SessionReportContext(
            status="ready",
            chunks=list(chunks),
            selectedChunkIds=[],
            appliedChunkIds=[],
            extractedUploadIds=["upload-1"],
        ),
    )


class TestReportContextSessionService:
    def test_select_report_context_chunks_accepts_legacy_string_custom_chunks(
        self,
    ) -> None:
        service = _service()
        now = datetime.now(UTC)
        existing_chunk = ReportContextChunk(
            id="chunk-1",
            sourceFileName="report.pdf",
            sourceIndex=0,
            title="Prior report",
            category="report_context",
            text="Existing extracted chunk",
            extractedAt=now,
        )
        session = _session_with_report_context(existing_chunk)

        selected = service._select_report_context_chunks(
            session,
            ["chunk-1"],
            ["Extra note from doctor."],
            reject_applied=True,
        )

        assert [chunk.id for chunk in selected[:1]] == ["chunk-1"]
        assert selected[1].text == "Extra note from doctor."
        assert selected[1].sourceFileName == "Custom note"
        assert selected[1].category == "custom_note"

    def test_report_context_upload_ids_for_chunks_tracks_only_successful_uploads(
        self,
    ) -> None:
        service = _service()
        now = datetime.now(UTC)
        chunks = [
            ReportContextChunk(
                id="chunk-1",
                sourceFileName="first.pdf",
                sourceIndex=0,
                title="First",
                category="report_context",
                text="Text",
                extractedAt=now,
            ),
            ReportContextChunk(
                id="chunk-2",
                sourceFileName="first.pdf",
                sourceIndex=0,
                title="First more",
                category="report_context",
                text="More text",
                extractedAt=now,
            ),
            ReportContextChunk(
                id="chunk-3",
                sourceFileName="third.pdf",
                sourceIndex=2,
                title="Third",
                category="report_context",
                text="Third text",
                extractedAt=now,
            ),
        ]

        selected_upload_ids = service._report_context_upload_ids_for_chunks(
            chunks,
            ["upload-1", "upload-2", "upload-3"],
        )

        assert selected_upload_ids == ["upload-1", "upload-3"]
