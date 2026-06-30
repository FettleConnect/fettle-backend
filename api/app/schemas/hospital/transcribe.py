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


class MigrationStatus(BaseModel):
    product: str
    phase: str
    implemented: list[str]
    pending: list[str]


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
    status: Literal[
        "waiting", "accepted", "in_consultation", "completed", "cancelled", "no_show"
    ]
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


class CreateSessionRequest(BaseModel):
    patientName: str
    patientPhoneNumber: str
    patientAge: int | None = None


class CreateSessionResponse(BaseModel):
    session: ConsultationSession


class ListSessionsResponse(BaseModel):
    sessions: list[ConsultationSession]


class GetSessionResponse(BaseModel):
    session: ConsultationSession


class GetPatientHistoryResponse(BaseModel):
    sessions: list[ConsultationSession]


class UpdateCaseStatusRequest(BaseModel):
    caseStatus: Literal["open", "closed"]


class DraftConsultationDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = None
    clinicalSummary: str | None = None
    medications: list[dict] = []
    testsPrescribed: list[dict] = []
    nextSteps: list[str] = []
    cautions: list[str] = []


class UploadSessionAudioResponse(BaseModel):
    session: ConsultationSession


class SubmitSessionTranscriptionResponse(BaseModel):
    session: ConsultationSession


class UpdateDraftDocumentResponse(BaseModel):
    session: ConsultationSession


class GenerateSessionPdfResponse(BaseModel):
    session: ConsultationSession


class ReportContextUpload(BaseModel):
    id: str
    fileName: str
    mimeType: str
    sizeBytes: int
    expiresAt: str


class UploadReportContextFileResponse(BaseModel):
    upload: ReportContextUpload


class ListReportContextUploadsResponse(BaseModel):
    uploads: list[ReportContextUpload]


class ExtractReportContextRequest(BaseModel):
    uploadIds: list[str]


class ExtractReportContextResponse(BaseModel):
    session: ConsultationSession


class ReportContextExtractionJob(BaseModel):
    id: str
    sessionId: str
    status: str
    uploadIds: list[str]
    totalUnits: int = 0
    completedUnits: int = 0
    failedUnits: int = 0
    chunks: list[dict] = []
    error: str | None = None
    createdAt: str
    startedAt: str | None = None
    updatedAt: str
    finishedAt: str | None = None
    progress: dict | None = None


class CreateReportContextExtractionJobResponse(BaseModel):
    job: ReportContextExtractionJob


class GetReportContextExtractionJobResponse(BaseModel):
    job: ReportContextExtractionJob


class GetActiveReportContextExtractionJobResponse(BaseModel):
    job: ReportContextExtractionJob | None
