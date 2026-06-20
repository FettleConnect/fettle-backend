from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.app.db.base import Base
from api.app.models.base import TenantMixin, TimestampMixin

# M2M: WellnessSession ↔ User (session join-interest requests)
session_requests = Table(
    "session_requests",
    Base.metadata,
    Column(
        "session_id",
        Uuid,
        ForeignKey("wellness_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    ),
)

# M2M: OutdoorActivity ↔ User (activity interest requests)
activity_requests = Table(
    "activity_requests",
    Base.metadata,
    Column(
        "activity_id",
        Uuid,
        ForeignKey("outdoor_activities.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    ),
)


# Convenience ORM aliases (for import in models/__init__.py)
SessionRequest = session_requests
ActivityRequest = activity_requests


class BodyComposition(TenantMixin, TimestampMixin, Base):
    """Body composition measurement for a corporate user."""

    __tablename__ = "body_compositions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    total_body_water: Mapped[float | None] = mapped_column(Float)
    protein: Mapped[float | None] = mapped_column(Float)
    body_fat_mass: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)
    bmi: Mapped[float | None] = mapped_column(Float)
    visceral_fat_level: Mapped[int | None] = mapped_column(Integer)
    fat_free_mass: Mapped[float | None] = mapped_column(Float)
    basal_metabolic_rate: Mapped[float | None] = mapped_column(Float)
    skeletal_muscle_mass: Mapped[float | None] = mapped_column(Float)
    recommended_calorie_intake: Mapped[float | None] = mapped_column(Float)
    metabolic_age: Mapped[int | None] = mapped_column(Integer)
    body_age: Mapped[int | None] = mapped_column(Integer)
    bone_mass: Mapped[float | None] = mapped_column(Float)


class WellnessVideo(TenantMixin, TimestampMixin, Base):
    """Corporate wellness video content."""

    __tablename__ = "wellness_videos"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    company: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int | None] = mapped_column(Integer)  # seconds
    category: Mapped[str | None] = mapped_column(String(100))
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    s3_link: Mapped[str | None] = mapped_column(Text, unique=True)
    rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rating_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # standard | leadership | women_only | lgbtq
    visibility: Mapped[str] = mapped_column(
        String(30), default="standard", nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WellnessSession(TenantMixin, TimestampMixin, Base):
    """
    Corporate wellness live/recorded session.
    M2M with User via session_requests (interest/join requests).
    """

    __tablename__ = "wellness_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    session_name: Mapped[str] = mapped_column(String(500), nullable=False)
    facilitator: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int | None] = mapped_column(Integer)  # minutes
    capacity: Mapped[int | None] = mapped_column(Integer)
    company: Mapped[str | None] = mapped_column(String(255))
    next_session: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    google_meet_link: Mapped[str | None] = mapped_column(Text)
    s3_link: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(30), default="standard", nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Therapist(TenantMixin, TimestampMixin, Base):
    """Corporate wellness therapist / specialist profile."""

    __tablename__ = "therapists"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Doctor | Nutritionist | Therapist | Yoga | Fitness Coach
    expert_type: Mapped[str | None] = mapped_column(String(50))
    specialty: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    rate_per_hour_usd: Mapped[float | None] = mapped_column(Numeric(10, 2))
    avg_duration: Mapped[int | None] = mapped_column(Integer)  # minutes
    whatsapp_number: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    hospital_contact: Mapped[str | None] = mapped_column(String(255))
    google_meet_link: Mapped[str | None] = mapped_column(Text)
    s3_link: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(30), default="standard", nullable=False
    )
    experience_years: Mapped[int | None] = mapped_column(Integer)
    hospital_affiliation: Mapped[str | None] = mapped_column(String(255))
    languages: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    qualifications: Mapped[str | None] = mapped_column(Text)
    reviews: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    availability_days: Mapped[str | None] = mapped_column(String(255))
    availability_time: Mapped[str | None] = mapped_column(String(255))


class Package(TenantMixin, TimestampMixin, Base):
    """Corporate wellness package offering."""

    __tablename__ = "packages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    original_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    discount_percentage: Mapped[float | None] = mapped_column(Numeric(5, 2))
    final_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    items: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    # HIGH | MEDIUM | LOW
    popularity: Mapped[str] = mapped_column(
        String(20), default="MEDIUM", nullable=False
    )
    s3_link: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(
        String(30), default="standard", nullable=False
    )


class OutdoorActivity(TenantMixin, TimestampMixin, Base):
    """Corporate outdoor group activity listing."""

    __tablename__ = "outdoor_activities"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    activity: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255))
    duration: Mapped[int | None] = mapped_column(Integer)  # minutes
    price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    group_link: Mapped[str | None] = mapped_column(Text)
    s3_link: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(
        String(30), default="standard", nullable=False
    )
    availability_days: Mapped[str | None] = mapped_column(String(255))
    availability_time: Mapped[str | None] = mapped_column(String(255))
