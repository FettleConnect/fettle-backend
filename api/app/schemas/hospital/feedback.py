from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class CallFeedbackIn(BaseModel):
    patient_id: uuid.UUID
    call_status: str
    call_outcome: str | None = None
    remarks: str = ""
    community_added: bool = False
    revisit_encouraged: bool = False
    escalation_required: bool = False
    call_duration: int = 0
    called_by: str | None = None
    called_at: datetime | None = None


class EscalationFeedbackIn(BaseModel):
    patient_id: uuid.UUID
    issue_description: str
    department: str | None = None


class CommunityFeedbackIn(BaseModel):
    patient_id: uuid.UUID
    engagement_type: str = "post"
    department: str | None = None


class UpdateEscalationIn(BaseModel):
    id: uuid.UUID
    status: str
    resolution_notes: str


class UpdateCommunityIn(BaseModel):
    id: uuid.UUID
    engagement_type: str
    department: str | None = None


class InboundCallFeedbackIn(BaseModel):
    patient_id: uuid.UUID  # inbound_call_id
    call_status: str
    call_outcome: str | None = None
    remarks: str = ""
    community_added: bool = False
    revisit_encouraged: bool = False
    escalation_required: bool = False
    call_duration: int = 0
    called_by: str | None = None
    called_at: datetime | None = None


class EscalationOut(BaseModel):
    id: uuid.UUID
    issue_description: str
    status: str
    department: str | None
    escalated_at: datetime
    resolved_at: datetime | None
    resolution_notes: str | None
    patient_name: str
    hospital_name: str


class EscalationListResponse(BaseModel):
    error: int = 0
    data: list[EscalationOut]


class SimpleOkResponse(BaseModel):
    error: int = 0
    msg: str
