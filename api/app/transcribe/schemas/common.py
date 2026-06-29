from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    error: str = Field(alias="message")
    code: str | None = None


class DoctorProfile(BaseModel):
    id: str
    name: str
    department: str | None = None
    phoneNumber: str | None = None
    email: str | None = None
    createdAt: datetime
    lastLoginAt: datetime


class PublicDoctorProfile(BaseModel):
    id: str
    name: str
    department: str | None = None


class PatientSummary(BaseModel):
    name: str
    phoneNumber: str
    age: int | None = None


class SessionAudioArtifact(BaseModel):
    fileName: str
    mimeType: str
    sizeBytes: int
    durationSeconds: float
    uploadedAt: datetime


class SessionTranscriptionDraftDocument(BaseModel):
    title: str | None = None
    clinicalSummary: str | None = None
    medications: list[dict] = []
    testsPrescribed: list[dict] = []
    nextSteps: list[str] = []
    cautions: list[str] = []


class TranscriptSegment(BaseModel):
    id: str
    speaker: str = "unknown"
    speakerLabel: str = "Consultation"
    text: str
    startedAtSeconds: int = 0
    endedAtSeconds: int | None = None


class SessionTranscription(BaseModel):
    status: str
    providerName: str | None = None
    requestedAt: datetime | None = None
    completedAt: datetime | None = None
    stage: str | None = None
    summary: str | None = None
    transcriptText: str | None = None
    transcriptSegments: list[TranscriptSegment] = []
    requiresRecovery: bool | None = None
    draftDocument: SessionTranscriptionDraftDocument | None = None


class DocumentArtifactDelivery(BaseModel):
    shareTitle: str
    shareMessage: str
    whatsappMessage: str


class DocumentArtifact(BaseModel):
    id: str
    fileName: str
    mimeType: str
    sizeBytes: int
    createdAt: datetime
    delivery: DocumentArtifactDelivery


class ReportContextChunk(BaseModel):
    id: str
    label: str | None = None
    pageNumber: int | None = None
    text: str


class ReportContextProgress(BaseModel):
    totalUnits: int
    completedUnits: int
    failedUnits: int
    startedAt: datetime
    updatedAt: datetime
    currentStage: str


class SessionReportContext(BaseModel):
    status: str
    chunks: list[ReportContextChunk] = []
    extractedUploadIds: list[str] = []
    progress: ReportContextProgress | None = None


class ConsultationSession(BaseModel):
    id: str
    doctorId: str
    patient: PatientSummary
    caseStatus: Literal["open", "closed"] = "open"
    status: str = "draft"
    createdAt: datetime
    updatedAt: datetime
    summary: str | None = None
    audioArtifact: SessionAudioArtifact | None = None
    transcription: SessionTranscription | None = None
    documentArtifact: DocumentArtifact | None = None
    reportContext: SessionReportContext | None = None
