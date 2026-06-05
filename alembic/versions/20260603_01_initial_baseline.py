"""initial baseline

Revision ID: 20260603_01
Revises:
Create Date: 2026-06-03 14:30:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)
    op.create_unique_constraint("uq_organizations_name", "organizations", ["name"])

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
    )
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"], unique=False)
    op.create_unique_constraint("uq_workspaces_org_slug", "workspaces", ["organization_id", "slug"])

    op.create_table(
        "user_organization_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
    )
    op.create_unique_constraint(
        "uq_user_org_membership",
        "user_organization_memberships",
        ["user_id", "organization_id"],
    )

    op.create_table(
        "stored_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_stored_files_storage_key", "stored_files", ["storage_key"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queue", sa.String(length=128), nullable=False),
        sa.Column("product", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"], unique=False)
    op.create_index("ix_jobs_product", "jobs", ["product"], unique=False)
    op.create_index("ix_jobs_queue", "jobs", ["queue"], unique=False)
    op.create_index("ix_jobs_status", "jobs", ["status"], unique=False)

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_attempts_status", "job_attempts", ["status"], unique=False)

    op.create_table(
        "kpi_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("metric_key", sa.String(length=255), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_kpi_events_created_at", "kpi_events", ["created_at"], unique=False)
    op.create_index("ix_kpi_events_metric_key", "kpi_events", ["metric_key"], unique=False)
    op.create_index("ix_kpi_events_section", "kpi_events", ["section"], unique=False)

    op.create_table(
        "transcribe_doctors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("department_slug", sa.String(length=255), nullable=False),
        sa.Column("doctor_name_slug", sa.String(length=255), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("department_slug", "doctor_name_slug", name="uq_transcribe_doctor_public_slug"),
    )
    op.create_index("ix_transcribe_doctors_department_slug", "transcribe_doctors", ["department_slug"], unique=False)
    op.create_index("ix_transcribe_doctors_doctor_name_slug", "transcribe_doctors", ["doctor_name_slug"], unique=False)

    op.create_table(
        "transcribe_intake_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doctor_id", sa.Integer(), sa.ForeignKey("transcribe_doctors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transcribe_intake_links_token", "transcribe_intake_links", ["token"], unique=True)

    op.create_table(
        "transcribe_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doctor_id", sa.Integer(), sa.ForeignKey("transcribe_doctors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_name", sa.String(length=255), nullable=True),
        sa.Column("patient_phone_number", sa.String(length=32), nullable=True),
        sa.Column("case_status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transcribe_sessions_case_status", "transcribe_sessions", ["case_status"], unique=False)
    op.create_index("ix_transcribe_sessions_created_at", "transcribe_sessions", ["created_at"], unique=False)
    op.create_index("ix_transcribe_sessions_doctor_id", "transcribe_sessions", ["doctor_id"], unique=False)
    op.create_index("ix_transcribe_sessions_patient_phone_number", "transcribe_sessions", ["patient_phone_number"], unique=False)
    op.create_index("ix_transcribe_sessions_status", "transcribe_sessions", ["status"], unique=False)

    op.create_table(
        "transcribe_session_audio",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stored_file_id", sa.Integer(), sa.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transcribe_session_audio_session_id", "transcribe_session_audio", ["session_id"], unique=False)

    op.create_table(
        "transcribe_transcriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("provider_name", sa.String(length=128), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("ix_transcribe_transcriptions_idempotency_key", "transcribe_transcriptions", ["idempotency_key"], unique=False)
    op.create_index("ix_transcribe_transcriptions_status", "transcribe_transcriptions", ["status"], unique=False)

    op.create_table(
        "transcribe_draft_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_id"),
    )

    op.create_table(
        "transcribe_report_context_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stored_file_id", sa.Integer(), sa.ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_transcribe_report_context_documents_session_id",
        "transcribe_report_context_documents",
        ["session_id"],
        unique=False,
    )
    op.create_index("ix_transcribe_report_context_documents_status", "transcribe_report_context_documents", ["status"], unique=False)

    op.create_table(
        "transcribe_report_context_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("transcribe_report_context_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=False),
    )

    op.create_table(
        "transcribe_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("stored_file_id", sa.Integer(), sa.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_id", "artifact_type", name="uq_transcribe_session_artifact_type"),
    )
    op.create_index("ix_transcribe_artifacts_artifact_type", "transcribe_artifacts", ["artifact_type"], unique=False)
    op.create_index("ix_transcribe_artifacts_session_id", "transcribe_artifacts", ["session_id"], unique=False)

    op.create_table(
        "transcribe_pdf_delivery_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transcribe_pdf_delivery_jobs_session_id", "transcribe_pdf_delivery_jobs", ["session_id"], unique=False)
    op.create_index("ix_transcribe_pdf_delivery_jobs_status", "transcribe_pdf_delivery_jobs", ["status"], unique=False)

    op.create_table(
        "transcribe_queue_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doctor_id", sa.Integer(), sa.ForeignKey("transcribe_doctors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_name", sa.String(length=255), nullable=False),
        sa.Column("patient_phone_number", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transcribe_queue_entries_doctor_id", "transcribe_queue_entries", ["doctor_id"], unique=False)
    op.create_index("ix_transcribe_queue_entries_status", "transcribe_queue_entries", ["status"], unique=False)

    op.create_table(
        "transcribe_sync_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="disabled"),
        sa.Column("target_system", sa.String(length=128), nullable=False, server_default="fettle_connect"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transcribe_sync_jobs_session_id", "transcribe_sync_jobs", ["session_id"], unique=False)
    op.create_index("ix_transcribe_sync_jobs_status", "transcribe_sync_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transcribe_sync_jobs_status", table_name="transcribe_sync_jobs")
    op.drop_index("ix_transcribe_sync_jobs_session_id", table_name="transcribe_sync_jobs")
    op.drop_table("transcribe_sync_jobs")

    op.drop_index("ix_transcribe_queue_entries_status", table_name="transcribe_queue_entries")
    op.drop_index("ix_transcribe_queue_entries_doctor_id", table_name="transcribe_queue_entries")
    op.drop_table("transcribe_queue_entries")

    op.drop_index("ix_transcribe_pdf_delivery_jobs_status", table_name="transcribe_pdf_delivery_jobs")
    op.drop_index("ix_transcribe_pdf_delivery_jobs_session_id", table_name="transcribe_pdf_delivery_jobs")
    op.drop_table("transcribe_pdf_delivery_jobs")

    op.drop_index("ix_transcribe_artifacts_session_id", table_name="transcribe_artifacts")
    op.drop_index("ix_transcribe_artifacts_artifact_type", table_name="transcribe_artifacts")
    op.drop_table("transcribe_artifacts")

    op.drop_table("transcribe_report_context_items")

    op.drop_index("ix_transcribe_report_context_documents_status", table_name="transcribe_report_context_documents")
    op.drop_index("ix_transcribe_report_context_documents_session_id", table_name="transcribe_report_context_documents")
    op.drop_table("transcribe_report_context_documents")

    op.drop_table("transcribe_draft_documents")

    op.drop_index("ix_transcribe_transcriptions_status", table_name="transcribe_transcriptions")
    op.drop_index("ix_transcribe_transcriptions_idempotency_key", table_name="transcribe_transcriptions")
    op.drop_table("transcribe_transcriptions")

    op.drop_index("ix_transcribe_session_audio_session_id", table_name="transcribe_session_audio")
    op.drop_table("transcribe_session_audio")

    op.drop_index("ix_transcribe_sessions_status", table_name="transcribe_sessions")
    op.drop_index("ix_transcribe_sessions_patient_phone_number", table_name="transcribe_sessions")
    op.drop_index("ix_transcribe_sessions_doctor_id", table_name="transcribe_sessions")
    op.drop_index("ix_transcribe_sessions_created_at", table_name="transcribe_sessions")
    op.drop_index("ix_transcribe_sessions_case_status", table_name="transcribe_sessions")
    op.drop_table("transcribe_sessions")

    op.drop_index("ix_transcribe_intake_links_token", table_name="transcribe_intake_links")
    op.drop_table("transcribe_intake_links")

    op.drop_index("ix_transcribe_doctors_doctor_name_slug", table_name="transcribe_doctors")
    op.drop_index("ix_transcribe_doctors_department_slug", table_name="transcribe_doctors")
    op.drop_table("transcribe_doctors")

    op.drop_index("ix_kpi_events_section", table_name="kpi_events")
    op.drop_index("ix_kpi_events_metric_key", table_name="kpi_events")
    op.drop_index("ix_kpi_events_created_at", table_name="kpi_events")
    op.drop_table("kpi_events")

    op.drop_index("ix_job_attempts_status", table_name="job_attempts")
    op.drop_table("job_attempts")

    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_queue", table_name="jobs")
    op.drop_index("ix_jobs_product", table_name="jobs")
    op.drop_index("ix_jobs_job_type", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_stored_files_storage_key", table_name="stored_files")
    op.drop_table("stored_files")

    op.drop_table("user_organization_memberships")

    op.drop_index("ix_workspaces_slug", table_name="workspaces")
    op.drop_table("workspaces")

    op.drop_constraint("uq_organizations_name", "organizations", type_="unique")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
