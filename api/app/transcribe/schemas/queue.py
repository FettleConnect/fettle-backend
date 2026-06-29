from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.app.transcribe.schemas.common import ConsultationSession


class QueuePatient(BaseModel):
    name: str
    phoneNumber: str = Field(pattern=r"^\d{10}$")
    age: int | None = None


class PatientProfile(BaseModel):
    name: str
    phoneNumber: str = Field(pattern=r"^\d{10}$")
    age: int | None = None


class IntakeLink(BaseModel):
    id: str
    doctorId: str
    doctorName: str
    label: str
    token: str
    isActive: bool
    createdAt: datetime
    updatedAt: datetime


class QueueEntry(BaseModel):
    id: str
    doctorId: str
    intakeLinkId: str
    intakeLabel: str
    patient: QueuePatient
    status: Literal["waiting", "accepted", "in_consultation", "completed", "cancelled", "no_show"]
    source: Literal["qr", "manual", "import"] = "qr"
    submittedAt: datetime
    acceptedAt: datetime | None = None
    sessionId: str | None = None
    createdAt: datetime
    updatedAt: datetime


class GetDoctorIntakeLinkResponse(BaseModel):
    intakeLink: IntakeLink


class GetPublicIntakeLinkResponse(BaseModel):
    intakeLink: IntakeLink


class SubmitPublicIntakeRequest(BaseModel):
    patient: QueuePatient


class SubmitPublicIntakeResponse(BaseModel):
    entry: QueueEntry


class ListQueueEntriesResponse(BaseModel):
    entries: list[QueueEntry]


class AcceptQueueEntryResponse(BaseModel):
    entry: QueueEntry
    session: ConsultationSession


class UpdateQueueEntryResponse(BaseModel):
    entry: QueueEntry


class GetPatientProfileByPhoneResponse(BaseModel):
    found: bool
    profile: PatientProfile | None = None
