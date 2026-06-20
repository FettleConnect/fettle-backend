from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TenantMixin, TimestampMixin


class SessionBooking(TenantMixin, TimestampMixin, Base):
    """Booking of a WellnessSession by a corporate user."""

    __tablename__ = "session_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("wellness_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # PENDING | BOOKED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    date: Mapped[date | None] = mapped_column(Date)
    user_email: Mapped[str | None] = mapped_column(String(255))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(String(255))
    s3_link: Mapped[str | None] = mapped_column(Text)


class TherapistBooking(TenantMixin, TimestampMixin, Base):
    """Booking of a Therapist session by a corporate user."""

    __tablename__ = "therapist_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    therapist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("therapists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    user_email: Mapped[str | None] = mapped_column(String(255))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    availability_days: Mapped[str | None] = mapped_column(String(255))
    availability_time: Mapped[str | None] = mapped_column(String(255))
    date: Mapped[date | None] = mapped_column(Date)


class ActivityBooking(TenantMixin, TimestampMixin, Base):
    """Booking of an OutdoorActivity by a corporate user."""

    __tablename__ = "activity_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    activity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("outdoor_activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    user_email: Mapped[str | None] = mapped_column(String(255))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    availability_days: Mapped[str | None] = mapped_column(String(255))
    availability_time: Mapped[str | None] = mapped_column(String(255))
    date: Mapped[date | None] = mapped_column(Date)
