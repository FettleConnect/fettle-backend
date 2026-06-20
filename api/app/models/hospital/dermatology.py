from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (DateTime, ForeignKey, String, Text, UniqueConstraint,
                        Uuid, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TenantMixin, TimestampMixin


class DermatologyPatient(TenantMixin, TimestampMixin, Base):
    """Dermatology chatbot patient — unique per (org, email)."""

    __tablename__ = "dermatology_patients"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_derm_patient_org_email"),
    )


class DermatologyThread(TenantMixin, TimestampMixin, Base):
    """
    A dermatology consultation thread.
    mode: general_education | post_payment_intake | dermatologist_review | final_output
    payment_status: unpaid | paid
    status: active | completed
    """

    __tablename__ = "dermatology_threads"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("dermatology_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str | None] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(
        String(30), default="general_education", nullable=False
    )
    payment_status: Mapped[str] = mapped_column(
        String(20), default="unpaid", nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # [{"role": "user", "content": "..."}, ...]
    conversation: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # {"symptoms": "...", "photos": [...]}
    intake_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    draft_response: Mapped[str | None] = mapped_column(Text)
