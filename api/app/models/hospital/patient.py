from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TenantMixin


class Patient(TenantMixin, Base):
    """
    Merges Django Patient_model and Patient_date_model.
    visit_date is nullable — null means the record has no explicit date (legacy batch).
    Unique constraint: (organization_id, mobile_no, department, visit_date).
    """

    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    serial_no: Mapped[int | None] = mapped_column(Integer)
    patient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer)
    mobile_no: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    visit_date: Mapped[date | None] = mapped_column(Date)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "mobile_no",
            "department",
            "visit_date",
            name="uq_patient_org_mobile_dept_date",
        ),
    )


class HospitalUploadLog(TenantMixin, Base):
    """Tracks CSV/Excel patient data uploads per hospital."""

    __tablename__ = "hospital_upload_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    # ["patients_2024_01.csv", ...]
    file_names: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # PENDING | SUCCESS | FAILED
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
