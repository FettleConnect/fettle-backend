"""expand_transcribe_report_context_state

Revision ID: 1c2f4e6a8b9d
Revises: b7c3a9d4e5f6
Create Date: 2026-06-30 09:45:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c2f4e6a8b9d"
down_revision: Union[str, None] = "b7c3a9d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcribe_sessions",
        sa.Column("report_context_state_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "transcribe_report_context_items",
        sa.Column("source_file_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "transcribe_report_context_items",
        sa.Column("source_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "transcribe_report_context_items",
        sa.Column("category", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "transcribe_report_context_items",
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcribe_report_context_items", "extracted_at")
    op.drop_column("transcribe_report_context_items", "category")
    op.drop_column("transcribe_report_context_items", "source_index")
    op.drop_column("transcribe_report_context_items", "source_file_name")
    op.drop_column("transcribe_sessions", "report_context_state_json")
