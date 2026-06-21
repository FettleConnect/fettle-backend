from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TenantMixin


class CallFeedback(TenantMixin, Base):
    """Feedback logged after an outbound call on a patient."""

    __tablename__ = "call_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # connected | not_connected
    call_status: Mapped[str] = mapped_column(String(20), nullable=False)
    # positive | negative | escalated | no_feedback
    call_outcome: Mapped[str | None] = mapped_column(String(20))
    remarks: Mapped[str | None] = mapped_column(Text)
    community_added: Mapped[bool] = mapped_column(Boolean, default=False)
    revisit_encouraged: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    call_duration: Mapped[int | None] = mapped_column(Integer)  # seconds
    called_by: Mapped[str | None] = mapped_column(String(255))
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Escalation(TenantMixin, Base):
    """Patient escalation record from outbound calls."""

    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    # pending | in-progress | resolved
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)


class CommunityEngagement(TenantMixin, Base):
    """Community engagement record linked to a patient from outbound calls."""

    __tablename__ = "community_engagements"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # post | comment | poll_participation | like | share
    engagement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    engagement_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    department: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OutboundCall(TenantMixin, Base):
    """
    LiveKit/Vobiz outbound call record per patient.
    Replaces legacy Outbound_Hospital (was VAPI-based).
    """

    __tablename__ = "outbound_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("patients.id", ondelete="SET NULL"), index=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("campaigns.id", ondelete="SET NULL"), index=True
    )
    livekit_call_id: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str | None] = mapped_column(String(50))
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    # not_happened | in_progress | connected | not_connected
    calling_process: Mapped[str] = mapped_column(
        String(20), default="not_happened", nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_s3_link: Mapped[str | None] = mapped_column(Text)
    audio_s3_link: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InboundCall(TenantMixin, Base):
    """
    LiveKit/Vobiz inbound call record — routed via vobiz_did lookup.
    Replaces legacy Inbound_Hospital.
    """

    __tablename__ = "inbound_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    livekit_call_id: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str | None] = mapped_column(String(50))
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    # not_happened | in_progress | connected | not_connected
    calling_process: Mapped[str] = mapped_column(
        String(20), default="not_happened", nullable=False
    )
    from_phone_number: Mapped[str | None] = mapped_column(String(20))
    to_phone_number: Mapped[str | None] = mapped_column(String(20))
    department: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_s3_link: Mapped[str | None] = mapped_column(Text)
    audio_s3_link: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InboundFeedback(TenantMixin, Base):
    """Call outcome feedback for an inbound call."""

    __tablename__ = "inbound_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    inbound_call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("inbound_calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    call_outcome: Mapped[str | None] = mapped_column(String(20))
    remarks: Mapped[str | None] = mapped_column(Text)
    community_added: Mapped[bool] = mapped_column(Boolean, default=False)
    revisit_encouraged: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    call_duration: Mapped[int | None] = mapped_column(Integer)  # seconds
    called_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InboundEscalation(TenantMixin, Base):
    """Escalation triggered from an inbound call."""

    __tablename__ = "inbound_escalations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    inbound_call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("inbound_calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)


class InboundCommunityEngagement(TenantMixin, Base):
    """Community engagement captured from an inbound call."""

    __tablename__ = "inbound_community_engagements"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    inbound_call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("inbound_calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    engagement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    engagement_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    department: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
