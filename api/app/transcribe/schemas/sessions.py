from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from api.app.transcribe.schemas.common import ConsultationSession


class CreateSessionRequest(BaseModel):
    patientName: str
    patientPhoneNumber: str
    patientAge: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_frontend_payload(cls, value: object) -> object:
        if not isinstance(value, dict) or "patient" not in value:
            return value
        patient = value.get("patient")
        if not isinstance(patient, dict):
            return value
        return {
            "patientName": patient.get("name"),
            "patientPhoneNumber": patient.get("phoneNumber"),
            "patientAge": patient.get("age"),
        }


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
