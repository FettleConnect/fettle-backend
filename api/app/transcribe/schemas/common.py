from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class DoctorSummary(BaseModel):
    name: str
    department: str | None = None
    phoneNumber: str | None = None
    email: str | None = None


class PatientSummary(BaseModel):
    name: str
    phoneNumber: str
    age: int | None = None


class SessionAudioArtifact(BaseModel):
    id: str
    fileName: str
    mimeType: str
    sizeBytes: int
    durationSeconds: float
    uploadedAt: datetime


class SessionTranscriptionDraftDocument(BaseModel):
    title: str | None = None
    chiefComplaint: str | None = None
    illnessHistory: str | None = None
    clinicalSummary: str | None = None
    medications: list[dict] = []
    testsPrescribed: list[str] = []
    revisitDate: str | None = None
    nextSteps: list[str] = []
    cautions: list[str] = []

    @field_validator("testsPrescribed", mode="before")
    @classmethod
    def _coerce_tests_prescribed(cls, value):
        if not isinstance(value, list):
            return value
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    normalized.append(cleaned)
            elif isinstance(item, dict):
                candidate = str(item.get("name") or item.get("testName") or "").strip()
                if candidate:
                    normalized.append(candidate)
        return normalized


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
    sourceFileName: str
    sourceIndex: int | None = None
    pageNumber: int | None = None
    title: str
    category: str
    text: str
    extractedAt: datetime
    label: str | None = None


class ReportContextProgress(BaseModel):
    totalUnits: int
    completedUnits: int
    failedUnits: int
    startedAt: datetime
    updatedAt: datetime
    etaSecondsLower: int | None = None
    etaSecondsUpper: int | None = None
    currentStage: str


class SessionReportContext(BaseModel):
    status: str
    chunks: list[ReportContextChunk] = []
    selectedChunkIds: list[str] = []
    appliedChunkIds: list[str] = []
    extractedUploadIds: list[str] = []
    progress: ReportContextProgress | None = None
    updatedAt: datetime | None = None
    error: str | None = None


class ConsultationSession(BaseModel):
    id: str
    doctorId: str
    patient: PatientSummary
    doctor: DoctorSummary
    caseStatus: Literal["open", "closed"] = "open"
    status: str = "draft"
    createdAt: datetime
    updatedAt: datetime
    summary: str | None = None
    audioArtifact: SessionAudioArtifact | None = None
    transcription: SessionTranscription | None = None
    documentArtifact: DocumentArtifact | None = None
    reportContext: SessionReportContext | None = None
