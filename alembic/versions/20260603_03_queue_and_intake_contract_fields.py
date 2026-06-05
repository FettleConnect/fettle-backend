"""queue and intake contract fields

Revision ID: 20260603_03
Revises: 20260603_02
Create Date: 2026-06-03 18:10:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_03"
down_revision = "20260603_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcribe_intake_links",
        sa.Column("label", sa.String(length=255), nullable=False, server_default="Clinic Queue"),
    )
    op.add_column(
        "transcribe_intake_links",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.add_column(
        "transcribe_queue_entries",
        sa.Column("intake_link_id", sa.Integer(), sa.ForeignKey("transcribe_intake_links.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "transcribe_queue_entries",
        sa.Column("intake_label", sa.String(length=255), nullable=False, server_default="Clinic Queue"),
    )
    op.add_column("transcribe_queue_entries", sa.Column("patient_age", sa.Integer(), nullable=True))
    op.add_column(
        "transcribe_queue_entries",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="qr"),
    )
    op.add_column(
        "transcribe_queue_entries",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column("transcribe_queue_entries", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "transcribe_queue_entries",
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("transcribe_sessions.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "transcribe_queue_entries",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index(
        "ix_transcribe_queue_entries_intake_link_id",
        "transcribe_queue_entries",
        ["intake_link_id"],
        unique=False,
    )

    op.execute("UPDATE transcribe_queue_entries SET status = 'waiting' WHERE status = 'pending'")
    op.execute("UPDATE transcribe_queue_entries SET submitted_at = created_at WHERE submitted_at IS NULL")
    op.execute("UPDATE transcribe_queue_entries SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    op.drop_index("ix_transcribe_queue_entries_intake_link_id", table_name="transcribe_queue_entries")
    op.drop_column("transcribe_queue_entries", "updated_at")
    op.drop_column("transcribe_queue_entries", "session_id")
    op.drop_column("transcribe_queue_entries", "accepted_at")
    op.drop_column("transcribe_queue_entries", "submitted_at")
    op.drop_column("transcribe_queue_entries", "source")
    op.drop_column("transcribe_queue_entries", "patient_age")
    op.drop_column("transcribe_queue_entries", "intake_label")
    op.drop_column("transcribe_queue_entries", "intake_link_id")
    op.drop_column("transcribe_intake_links", "updated_at")
    op.drop_column("transcribe_intake_links", "label")
