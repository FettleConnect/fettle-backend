from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class PatientOut(BaseModel):
    id: uuid.UUID
    patient_name: str
    mobile_no: str
    department: str | None = None
    age: int | None = None
    hospital_name: str
    whatsapp_link: str
    calling_progress: str
    color: str


class PatientListResponse(BaseModel):
    error: int = 0
    data: list[PatientOut]
    count: int


class CallHistoryItem(BaseModel):
    id: str
    type: str  # outbound | inbound
    started_at: datetime | None = None
    status: str | None = None
    transcript_link: str | None = None
    audio_link: str | None = None
    summary: str | None = None


class PatientCallHistoryResponse(BaseModel):
    error: int = 0
    history: list[CallHistoryItem]


class UploadLogOut(BaseModel):
    id: str
    file_names: list[str]
    status: str
    uploaded_at: datetime
    message: str | None = None


class UploadLogsResponse(BaseModel):
    error: int = 0
    data: list[UploadLogOut]
