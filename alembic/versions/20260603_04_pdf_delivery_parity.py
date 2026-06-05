"""pdf delivery parity

Revision ID: 20260603_04
Revises: 20260603_03
Create Date: 2026-06-03 19:20:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_04"
down_revision = "20260603_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcribe_pdf_delivery_jobs",
        sa.Column("recipient_type", sa.String(length=32), nullable=False, server_default="patient"),
    )
    op.add_column(
        "transcribe_pdf_delivery_jobs",
        sa.Column("recipient_name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "transcribe_pdf_delivery_jobs",
        sa.Column("phone_number", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "transcribe_pdf_delivery_jobs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("transcribe_pdf_delivery_jobs", sa.Column("provider", sa.String(length=128), nullable=True))
    op.add_column("transcribe_pdf_delivery_jobs", sa.Column("provider_message_id", sa.String(length=255), nullable=True))
    op.add_column("transcribe_pdf_delivery_jobs", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("transcribe_pdf_delivery_jobs", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "transcribe_pdf_delivery_jobs",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_transcribe_pdf_delivery_jobs_provider_message_id",
        "transcribe_pdf_delivery_jobs",
        ["provider_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_transcribe_pdf_delivery_jobs_recipient_type",
        "transcribe_pdf_delivery_jobs",
        ["recipient_type"],
        unique=False,
    )

    op.create_table(
        "transcribe_pdf_delivery_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("pharmacy_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pharmacy_name", sa.String(length=255), nullable=True),
        sa.Column("pharmacy_phone_number", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_transcribe_pdf_delivery_settings_session_id",
        "transcribe_pdf_delivery_settings",
        ["session_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_transcribe_pdf_delivery_settings_session_id", table_name="transcribe_pdf_delivery_settings")
    op.drop_table("transcribe_pdf_delivery_settings")
    op.drop_index("ix_transcribe_pdf_delivery_jobs_recipient_type", table_name="transcribe_pdf_delivery_jobs")
    op.drop_index("ix_transcribe_pdf_delivery_jobs_provider_message_id", table_name="transcribe_pdf_delivery_jobs")
    op.drop_column("transcribe_pdf_delivery_jobs", "updated_at")
    op.drop_column("transcribe_pdf_delivery_jobs", "sent_at")
    op.drop_column("transcribe_pdf_delivery_jobs", "last_error")
    op.drop_column("transcribe_pdf_delivery_jobs", "provider_message_id")
    op.drop_column("transcribe_pdf_delivery_jobs", "provider")
    op.drop_column("transcribe_pdf_delivery_jobs", "attempt_count")
    op.drop_column("transcribe_pdf_delivery_jobs", "phone_number")
    op.drop_column("transcribe_pdf_delivery_jobs", "recipient_name")
    op.drop_column("transcribe_pdf_delivery_jobs", "recipient_type")
