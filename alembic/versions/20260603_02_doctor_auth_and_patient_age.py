"""doctor auth and patient age

Revision ID: 20260603_02
Revises: 20260603_01
Create Date: 2026-06-03 15:15:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_02"
down_revision = "20260603_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcribe_doctors", sa.Column("department", sa.String(length=255), nullable=True))
    op.add_column("transcribe_doctors", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column(
        "transcribe_doctors",
        sa.Column("last_login_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column(
        "transcribe_doctors",
        sa.Column("pin_code", sa.String(length=16), server_default="123456", nullable=False),
    )
    op.add_column(
        "transcribe_doctors",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "transcribe_doctors",
        sa.Column("must_change_pin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("transcribe_sessions", sa.Column("patient_age", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("transcribe_sessions", "patient_age")
    op.drop_column("transcribe_doctors", "must_change_pin")
    op.drop_column("transcribe_doctors", "token_version")
    op.drop_column("transcribe_doctors", "pin_code")
    op.drop_column("transcribe_doctors", "last_login_at")
    op.drop_column("transcribe_doctors", "email")
    op.drop_column("transcribe_doctors", "department")
