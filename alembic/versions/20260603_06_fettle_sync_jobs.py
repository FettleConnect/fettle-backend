"""fettle sync jobs

Revision ID: 20260603_06
Revises: 20260603_05
Create Date: 2026-06-03 20:45:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_06"
down_revision = "20260603_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("transcribe_sync_jobs", "session_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "transcribe_sync_jobs",
        sa.Column("trigger", sa.String(length=64), nullable=False, server_default="transcription_completed"),
    )
    op.add_column("transcribe_sync_jobs", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column("transcribe_sync_jobs", sa.Column("external_session_id", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("transcribe_sync_jobs", sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("transcribe_sync_jobs", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("transcribe_sync_jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="10"))
    op.add_column(
        "transcribe_sync_jobs",
        sa.Column("first_queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "transcribe_sync_jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "transcribe_sync_jobs",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column("transcribe_sync_jobs", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("transcribe_sync_jobs", sa.Column("last_error_class", sa.String(length=128), nullable=True))
    op.add_column("transcribe_sync_jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE transcribe_sync_jobs "
        "SET idempotency_key = 'legacy-sync-key:' || id::text, external_session_id = COALESCE(session_id::text, '') "
        "WHERE idempotency_key IS NULL"
    )
    op.alter_column("transcribe_sync_jobs", "idempotency_key", existing_type=sa.String(length=255), nullable=False)
    op.create_index("ix_transcribe_sync_jobs_trigger", "transcribe_sync_jobs", ["trigger"], unique=False)
    op.create_index("ix_transcribe_sync_jobs_idempotency_key", "transcribe_sync_jobs", ["idempotency_key"], unique=True)
    op.create_index("ix_transcribe_sync_jobs_next_attempt_at", "transcribe_sync_jobs", ["next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transcribe_sync_jobs_next_attempt_at", table_name="transcribe_sync_jobs")
    op.drop_index("ix_transcribe_sync_jobs_idempotency_key", table_name="transcribe_sync_jobs")
    op.drop_index("ix_transcribe_sync_jobs_trigger", table_name="transcribe_sync_jobs")
    op.drop_column("transcribe_sync_jobs", "finished_at")
    op.drop_column("transcribe_sync_jobs", "last_error_class")
    op.drop_column("transcribe_sync_jobs", "last_error")
    op.drop_column("transcribe_sync_jobs", "updated_at")
    op.drop_column("transcribe_sync_jobs", "next_attempt_at")
    op.drop_column("transcribe_sync_jobs", "first_queued_at")
    op.drop_column("transcribe_sync_jobs", "max_attempts")
    op.drop_column("transcribe_sync_jobs", "attempts")
    op.drop_column("transcribe_sync_jobs", "payload_json")
    op.drop_column("transcribe_sync_jobs", "external_session_id")
    op.drop_column("transcribe_sync_jobs", "idempotency_key")
    op.drop_column("transcribe_sync_jobs", "trigger")
    op.execute("DELETE FROM transcribe_sync_jobs WHERE session_id IS NULL")
    op.alter_column("transcribe_sync_jobs", "session_id", existing_type=sa.Integer(), nullable=False)
