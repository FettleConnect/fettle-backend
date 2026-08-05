from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    UUID,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimestampUpdateMixin(TimestampMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class HospitalModel(Base, TimestampMixin):
    __tablename__ = "hospitals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    reception_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pharmacy_email: Mapped[str | None] = mapped_column(String(255), nullable=True)


class HospitalUserModel(Base, TimestampMixin):
    __tablename__ = "hospital_users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    patient_engagement: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    community_egagement: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    revisit_engagement: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    escalation_engagement: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    calllog_engagement: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    upload_engagement: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    pdf_engagement: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AdminModel(Base, TimestampMixin):
    __tablename__ = "admins"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)


class DoctorModel(Base, TimestampMixin):
    __tablename__ = "doctors"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mobile_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department: Mapped[str] = mapped_column(String(255), nullable=False)
    availability: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    timings: Mapped[str | None] = mapped_column(String(255), nullable=True)
    consultation_fee: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PatientModel(Base, TimestampMixin):
    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint(
            "hospital_id",
            "mobile_no",
            "department",
            name="uq_patient_hospital_mobile_department",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    serial_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    patient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_no: Mapped[str] = mapped_column(String(50), nullable=False)
    department: Mapped[str] = mapped_column(String(255), nullable=False)


class PatientDateModel(Base, TimestampMixin):
    __tablename__ = "patient_dates"
    __table_args__ = (
        UniqueConstraint(
            "hospital_id",
            "mobile_no",
            "department",
            "date",
            name="uq_patient_date_hospital_mobile_department_date",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    serial_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    patient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_no: Mapped[str] = mapped_column(String(50), nullable=False)
    department: Mapped[str] = mapped_column(String(255), nullable=False)


class UploadStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class HospitalUploadLog(Base, TimestampMixin):
    __tablename__ = "hospital_upload_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    file_names: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=UploadStatus.PENDING.value, nullable=False
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class CallStatus(str, Enum):
    connected = "connected"
    not_connected = "not_connected"


class CallOutcome(str, Enum):
    positive = "positive"
    negative = "negative"
    escalated = "escalated"
    no_feedback = "no_feedback"


class EngagementType(str, Enum):
    post = "post"
    comment = "comment"
    poll_participation = "poll_participation"
    like = "like"
    share = "share"


class EscalationStatus(str, Enum):
    pending = "pending"
    in_progress = "in-progress"
    resolved = "resolved"


class CallFeedbackModel(Base, TimestampMixin):
    __tablename__ = "call_feedback"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id"), nullable=False
    )
    patient: Mapped[PatientModel] = relationship()
    call_status: Mapped[str] = mapped_column(String(20), nullable=False)
    call_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    community_added: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    revisit_encouraged: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    escalation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    call_duration: Mapped[str | None] = mapped_column(String(255), nullable=True)
    called_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    called_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EscalationModel(Base, TimestampMixin):
    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id"), nullable=False
    )
    patient: Mapped[PatientModel] = relationship()
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CommunityEngagementModel(Base, TimestampMixin):
    __tablename__ = "community_engagements"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id"), nullable=False
    )
    patient: Mapped[PatientModel] = relationship()
    engagement_type: Mapped[str] = mapped_column(String(50), nullable=False)
    engagement_date: Mapped[date] = mapped_column(Date, nullable=False)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)


class InboundHospital(Base, TimestampMixin):
    __tablename__ = "inbound_hospitals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id"), nullable=True
    )
    hospital: Mapped[HospitalModel | None] = relationship()
    vapi_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    endedReason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message_s3_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    audio_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_id_process: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calling_process: Mapped[str] = mapped_column(
        String(20), default="not_happened", nullable=False
    )
    from_phone_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_phone_numnber: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department: Mapped[str | None] = mapped_column(
        String(255), default="N/A", nullable=True
    )


class CallFeedbackModelInbound(Base, TimestampMixin):
    __tablename__ = "call_feedback_inbound"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_hospitals.id"), nullable=False
    )
    patient: Mapped[InboundHospital] = relationship()
    call_status: Mapped[str] = mapped_column(String(20), nullable=False)
    call_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    community_added: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    revisit_encouraged: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    escalation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    call_duration: Mapped[str | None] = mapped_column(String(255), nullable=True)
    called_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    called_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CommunityEngagementModelInbound(Base, TimestampMixin):
    __tablename__ = "community_engagements_inbound"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_hospitals.id"), nullable=False
    )
    patient: Mapped[InboundHospital] = relationship()
    engagement_type: Mapped[str] = mapped_column(String(50), nullable=False)
    engagement_date: Mapped[date] = mapped_column(Date, nullable=False)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EscalationModelInbound(Base, TimestampMixin):
    __tablename__ = "escalations_inbound"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_hospitals.id"), nullable=False
    )
    patient: Mapped[InboundHospital] = relationship()
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TextModel(Base, TimestampMixin):
    __tablename__ = "text_templates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class OutboundAssistant(Base, TimestampMixin):
    __tablename__ = "outbound_assistants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    assistant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    hospital: Mapped[HospitalModel] = relationship()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_type: Mapped[str] = mapped_column(String(50), nullable=False)
    audience_type: Mapped[str] = mapped_column(
        String(50), default="all", nullable=False
    )
    patient_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    package_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    facility_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discount_details: Mapped[str | None] = mapped_column(String(255), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    unconnected_only: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)


class OutboundHospital(Base, TimestampMixin):
    __tablename__ = "outbound_hospitals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    vapi_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outbound_assistants.id"), nullable=True
    )
    assistant: Mapped[OutboundAssistant | None] = relationship()
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True
    )
    patient: Mapped[PatientModel | None] = relationship()
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True
    )
    campaign: Mapped[Campaign | None] = relationship()
    endedReason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message_s3_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    audio_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_id_process: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calling_process: Mapped[str] = mapped_column(
        String(20), default="not_happened", nullable=False
    )


class GlobalConfig(Base, TimestampUpdateMixin):
    __tablename__ = "global_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class DermatologyPatient(Base, TimestampUpdateMixin):
    __tablename__ = "dermatology_patients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class ThreadMode(str, Enum):
    general_education = "general_education"
    post_payment_intake = "post_payment_intake"
    dermatologist_review = "dermatologist_review"
    final_output = "final_output"


class PaymentStatus(str, Enum):
    unpaid = "unpaid"
    paid = "paid"


class ThreadStatus(str, Enum):
    active = "active"
    completed = "completed"


class DermatologyThread(Base, TimestampUpdateMixin):
    __tablename__ = "dermatology_threads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dermatology_patients.id"), nullable=False
    )
    patient: Mapped[DermatologyPatient] = relationship()
    name: Mapped[str] = mapped_column(
        String(255), default="Consultation", nullable=False
    )
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    conversation: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    intake_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    draft_response: Mapped[str | None] = mapped_column(Text, nullable=True)


class MediVoiceSession(Base, TimestampMixin):
    __tablename__ = "medivoice_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doctors.id"), nullable=False
    )
    doctor: Mapped[DoctorModel] = relationship()
    patient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    patient_mobile: Mapped[str] = mapped_column(String(50), nullable=False)
    patient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    overall_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    medicines: Mapped[list | None] = mapped_column(JSON, nullable=True)
    revisit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    revisit_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    meta_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MediVoiceTranscription(Base, TimestampMixin):
    __tablename__ = "medivoice_transcriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medivoice_sessions.id"), nullable=False
    )
    session: Mapped[MediVoiceSession] = relationship()
    speaker: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


CallFeedback = CallFeedbackModel
OutboundCall = OutboundHospital
Patient = PatientModel
User = HospitalUserModel

__all__ = [
    "AdminModel",
    "Base",
    "CallFeedback",
    "CallFeedbackModel",
    "CallFeedbackModelInbound",
    "Campaign",
    "CommunityEngagementModel",
    "CommunityEngagementModelInbound",
    "DermatologyPatient",
    "DermatologyThread",
    "DoctorModel",
    "EscalationModel",
    "EscalationModelInbound",
    "GlobalConfig",
    "HospitalModel",
    "HospitalUploadLog",
    "HospitalUserModel",
    "InboundHospital",
    "MediVoiceSession",
    "MediVoiceTranscription",
    "OutboundAssistant",
    "OutboundCall",
    "OutboundHospital",
    "Patient",
    "PatientDateModel",
    "PatientModel",
    "TextModel",
    "User",
]
