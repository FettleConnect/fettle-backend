from __future__ import annotations

from api.app.models.auth import User
from api.app.models.corporate.booking import (
    ActivityBooking,
    SessionBooking,
    TherapistBooking,
)
from api.app.models.corporate.social import (
    Comment,
    CommentLike,
    Notification,
    Post,
    PostLike,
    SupportTicket,
    UserSavedPost,
)
from api.app.models.corporate.wellness import (
    ActivityRequest,
    BodyComposition,
    OutdoorActivity,
    Package,
    SessionRequest,
    Therapist,
    WellnessSession,
    WellnessVideo,
)
from api.app.models.hospital.call import (
    CallFeedback,
    CommunityEngagement,
    Escalation,
    InboundCall,
    InboundCommunityEngagement,
    InboundEscalation,
    InboundFeedback,
    OutboundCall,
)
from api.app.models.hospital.campaign import Campaign
from api.app.models.hospital.dermatology import DermatologyPatient, DermatologyThread
from api.app.models.hospital.medivoice import MediVoiceSession, MediVoiceTranscription
from api.app.models.hospital.patient import HospitalUploadLog, Patient
from api.app.models.organization import CorporateConfig, HospitalConfig, Organization
from api.app.models.shared import GlobalConfig

__all__ = [
    "Organization",
    "HospitalConfig",
    "CorporateConfig",
    "User",
    "Patient",
    "HospitalUploadLog",
    "CallFeedback",
    "Escalation",
    "CommunityEngagement",
    "OutboundCall",
    "InboundCall",
    "InboundFeedback",
    "InboundEscalation",
    "InboundCommunityEngagement",
    "Campaign",
    "MediVoiceSession",
    "MediVoiceTranscription",
    "DermatologyPatient",
    "DermatologyThread",
    "BodyComposition",
    "WellnessVideo",
    "WellnessSession",
    "SessionRequest",
    "Therapist",
    "Package",
    "OutdoorActivity",
    "ActivityRequest",
    "SessionBooking",
    "TherapistBooking",
    "ActivityBooking",
    "Post",
    "UserSavedPost",
    "Comment",
    "PostLike",
    "CommentLike",
    "Notification",
    "SupportTicket",
    "GlobalConfig",
]
