from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base


class StoredFile(Base):
    __tablename__ = "stored_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    queue: Mapped[str] = mapped_column(String(128), index=True)
    product: Mapped[str] = mapped_column(String(64), index=True)
    job_type: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True, default="pending")
    payload_json: Mapped[str] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class JobAttempt(Base):
    __tablename__ = "job_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(64), index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class KpiEvent(Base):
    __tablename__ = "kpi_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_key: Mapped[str] = mapped_column(String(255), index=True)
    section: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class TranscribeIntakeLink(Base):
    __tablename__ = "transcribe_intake_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(255), default="Clinic Queue")
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TranscribeSession(Base):
    __tablename__ = "transcribe_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    patient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    patient_phone_number: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )
    patient_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TranscribeSessionAudio(Base):
    __tablename__ = "transcribe_session_audio"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), index=True
    )
    stored_file_id: Mapped[int] = mapped_column(
        ForeignKey("stored_files.id", ondelete="CASCADE")
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TranscribeTranscription(Base):
    __tablename__ = "transcribe_transcriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), unique=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    provider_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_segments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    requires_recovery: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TranscribeDraftDocument(Base):
    __tablename__ = "transcribe_draft_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), unique=True
    )
    content_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TranscribeReportContextDocument(Base):
    __tablename__ = "transcribe_report_context_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), index=True
    )
    stored_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TranscribeReportContextItem(Base):
    __tablename__ = "transcribe_report_context_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_report_context_documents.id", ondelete="CASCADE")
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text)


class TranscribeArtifact(Base):
    __tablename__ = "transcribe_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), index=True)
    stored_file_id: Mapped[int] = mapped_column(
        ForeignKey("stored_files.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id", "artifact_type", name="uq_transcribe_session_artifact_type"
        ),
    )


class TranscribePdfDeliveryJob(Base):
    __tablename__ = "transcribe_pdf_delivery_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    destination: Mapped[str] = mapped_column(String(255))
    recipient_type: Mapped[str] = mapped_column(
        String(32), default="patient", index=True
    )
    recipient_name: Mapped[str] = mapped_column(String(255), default="")
    phone_number: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TranscribePdfDeliverySettings(Base):
    __tablename__ = "transcribe_pdf_delivery_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    patient_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    pharmacy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pharmacy_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pharmacy_phone_number: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TranscribeQueueEntry(Base):
    __tablename__ = "transcribe_queue_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    intake_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcribe_intake_links.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    intake_label: Mapped[str] = mapped_column(String(255), default="Clinic Queue")
    patient_name: Mapped[str] = mapped_column(String(255))
    patient_phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    patient_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="waiting", index=True)
    source: Mapped[str] = mapped_column(String(32), default="qr")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TranscribeSyncJob(Base):
    __tablename__ = "transcribe_sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcribe_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(64), default="disabled", index=True)
    target_system: Mapped[str] = mapped_column(
        String(128), default="fettle_connect"
    )
    trigger: Mapped[str] = mapped_column(
        String(64), default="transcription_completed", index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    external_session_id: Mapped[str] = mapped_column(String(255), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=10)
    first_queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_class: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
