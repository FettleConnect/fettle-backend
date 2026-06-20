from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text, Time, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TenantMixin


class MediVoiceSession(TenantMixin, Base):
    """
    AI-assisted doctor consultation session (MediVoice product).
    Links to the doctor user record, not the patient table —
    the patient here is an ad-hoc appointment contact, not a bulk-upload patient.
    """

    __tablename__ = "medivoice_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_name: Mapped[str | None] = mapped_column(String(255))
    patient_mobile: Mapped[str | None] = mapped_column(String(20))
    patient_email: Mapped[str | None] = mapped_column(String(255))
    overall_summary: Mapped[str | None] = mapped_column(Text)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    # [{"name": "Paracetamol", "dose": "500mg", "frequency": "TID"}]
    medicines: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    revisit_date: Mapped[date | None] = mapped_column(Date)
    revisit_time: Mapped[time | None] = mapped_column(Time)
    # {"bp": "120/80", "findings": [...]}
    meta_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MediVoiceTranscription(TenantMixin, Base):
    """Individual utterance in a MediVoice session."""

    __tablename__ = "medivoice_transcriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("medivoice_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # doctor | patient
    speaker: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
