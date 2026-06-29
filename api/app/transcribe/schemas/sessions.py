from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from api.app.transcribe.schemas.common import ConsultationSession


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
