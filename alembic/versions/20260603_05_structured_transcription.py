"""structured transcription

Revision ID: 20260603_05
Revises: 20260603_04
Create Date: 2026-06-03 20:05:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_05"
down_revision = "20260603_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcribe_transcriptions", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("transcribe_transcriptions", sa.Column("transcript_segments_json", sa.Text(), nullable=True))
    op.add_column("transcribe_transcriptions", sa.Column("metadata_json", sa.Text(), nullable=True))
    op.add_column("transcribe_transcriptions", sa.Column("stage", sa.String(length=128), nullable=True))
    op.add_column("transcribe_transcriptions", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "transcribe_transcriptions",
        sa.Column("requires_recovery", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("transcribe_transcriptions", "requires_recovery")
    op.drop_column("transcribe_transcriptions", "attempt_count")
    op.drop_column("transcribe_transcriptions", "stage")
    op.drop_column("transcribe_transcriptions", "metadata_json")
    op.drop_column("transcribe_transcriptions", "transcript_segments_json")
    op.drop_column("transcribe_transcriptions", "summary")
