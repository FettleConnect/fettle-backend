from __future__ import annotations

import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, Query, Request, Response, WebSocket

from api.app.transcribe.api.dependencies import (
    get_auth_service,
    get_fettle_sync_service,
    get_live_transcription_service,
    get_observability_service,
    get_pdf_delivery_service,
    get_queue_service,
    get_session_service,
    require_doctor,
    require_internal_admin,
)
from api.app.transcribe.observability import ObservabilityService
from api.app.transcribe.schemas.auth import (
    AuthMeResponse,
    ListDoctorsResponse,
    PinChangeRequest,
    PinLoginAuthenticatedResponse,
    PinLoginPinChangeRequiredResponse,
    PinLoginRequest,
    SelectDoctorSessionRequest,
)
from api.app.transcribe.schemas.health import MigrationStatus
from api.app.transcribe.schemas.queue import (
    AcceptQueueEntryResponse,
    GetDoctorIntakeLinkResponse,
    GetPatientProfileByPhoneResponse,
    GetPublicIntakeLinkResponse,
)
from api.app.transcribe.schemas.queue import (
    ListQueueEntriesResponse as QueueListResponse,
)
from api.app.transcribe.schemas.queue import (
    SubmitPublicIntakeRequest,
    SubmitPublicIntakeResponse,
    UpdateQueueEntryResponse,
)
from api.app.transcribe.schemas.sessions import (
    CreateReportContextExtractionJobResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DraftConsultationDocument,
    ExtractReportContextRequest,
    ExtractReportContextResponse,
    GenerateSessionPdfResponse,
    GetActiveReportContextExtractionJobResponse,
    GetPatientHistoryResponse,
    GetReportContextExtractionJobResponse,
    GetSessionResponse,
    ListReportContextUploadsResponse,
    ListSessionsResponse,
    SubmitSessionTranscriptionResponse,
    UpdateCaseStatusRequest,
    UpdateDraftDocumentResponse,
    UploadReportContextFileResponse,
    UploadSessionAudioResponse,
)
from api.app.transcribe.services.auth import AuthService
from api.app.transcribe.services.fettle_sync import FettleSyncService
from api.app.transcribe.services.live_transcription import LiveTranscriptionService
from api.app.transcribe.services.pdf_delivery import PdfDeliveryService
from api.app.transcribe.services.queue import QueueService
from api.app.transcribe.services.sessions import SessionService
from api.app.transcribe.storage import LocalStorage

router = APIRouter(tags=["transcribe"])
SAMPLE_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _json_error_response(status_code: int, message: str) -> Response:
    return Response(
        status_code=status_code,
        content=f'{{"error":"{message}"}}',
        media_type="application/json",
    )


def _build_live_preview_segments(
    transcript_segments: list[dict], *, is_final: bool
) -> list[dict]:
    return [
        {
            "id": str(uuid.uuid4()),
            "speaker": segment.get("speaker", "unknown"),
            "speakerLabel": segment.get("speakerLabel", "Speaker"),
            "text": segment.get("text", ""),
            "startedAtSeconds": segment.get("startedAtSeconds", 0),
            "endedAtSeconds": segment.get(
                "endedAtSeconds", segment.get("startedAtSeconds", 0)
            ),
            "isFinal": is_final,
        }
        for segment in transcript_segments
    ]


def _build_mock_live_preview(session) -> list[dict]:
    patient_label = session.patient.name or "Patient"
    doctor_label = session.doctor.name or "Doctor"
    return _build_live_preview_segments(
        [
            {
                "speaker": "doctor",
                "speakerLabel": doctor_label,
                "text": f"Good morning {patient_label}. Tell me what has been bothering you today.",
                "startedAtSeconds": 0,
                "endedAtSeconds": 5,
            },
            {
                "speaker": "patient",
                "speakerLabel": patient_label,
                "text": "I have had a persistent cough, throat irritation, and mild fever for the last three days.",
                "startedAtSeconds": 5,
                "endedAtSeconds": 11,
            },
        ],
        is_final=False,
    )


@router.get("/internal/migration-status", response_model=MigrationStatus)
async def migration_status() -> MigrationStatus:
    return MigrationStatus(
        product="transcribe",
        phase="scaffold",
        implemented=[
            "fastapi-app",
            "health-routes",
            "shared-module-boundaries",
            "postgres-compose",
            "plan-doc",
            "route-inventory",
            "schema-baseline",
            "reference-import",
            "auth-session-compatibility-skeleton",
            "audio-transcription-routes",
            "artifact-routes",
            "report-context-routes",
            "public-intake-routes",
            "queue-routes",
            "admin-routes",
            "sync-adapter-routes",
            "kpi-reliability-routes",
            "pdf-delivery-routes",
            "report-context-extended-routes",
            "pin-change-routes",
            "admin-session-mutation-routes",
        ],
        pending=[],
    )


@router.get("/doctors", response_model=ListDoctorsResponse, tags=["auth"])
async def list_doctors(
    search: str | None = Query(default=None),
    auth_service: AuthService = Depends(get_auth_service),
) -> ListDoctorsResponse:
    return await auth_service.list_doctors(search)


@router.post("/auth/select", tags=["auth"])
async def select_doctor(
    request: SelectDoctorSessionRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    return await auth_service.select_doctor(request.doctorId)


@router.post(
    "/auth/pin/login",
    response_model=PinLoginAuthenticatedResponse | PinLoginPinChangeRequiredResponse,
    response_model_exclude_none=True,
    tags=["auth"],
)
async def login_with_pin(
    request: PinLoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    return await auth_service.login_with_pin(request.doctorId, request.pin)


@router.post("/auth/pin/change", response_model_exclude_none=True, tags=["auth"])
async def change_pin(
    request: PinChangeRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    return await auth_service.change_pin(request.pinChangeToken, request.newPin)


@router.websocket("/live-transcription")
async def live_transcription_socket(
    websocket: WebSocket,
    live_transcription_service: LiveTranscriptionService = Depends(
        get_live_transcription_service
    ),
) -> None:
    await live_transcription_service.handle(websocket)


@router.get(
    "/auth/me",
    response_model=AuthMeResponse,
    response_model_exclude_none=True,
    tags=["auth"],
)
async def auth_me(doctor=Depends(require_doctor)) -> AuthMeResponse:
    return AuthMeResponse(doctor=doctor)


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    response_model_exclude_none=True,
    status_code=201,
    tags=["sessions"],
)
async def create_session(
    request: CreateSessionRequest,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> CreateSessionResponse:
    return CreateSessionResponse(
        session=await session_service.create_session(doctor.id, request)
    )


@router.get(
    "/sessions",
    response_model=ListSessionsResponse,
    response_model_exclude_none=True,
    tags=["sessions"],
)
async def list_sessions(
    search: str | None = Query(default=None),
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> ListSessionsResponse:
    return ListSessionsResponse(
        sessions=await session_service.list_sessions(doctor.id, search)
    )


@router.get(
    "/sessions/{session_id}",
    response_model=GetSessionResponse,
    response_model_exclude_none=True,
    tags=["sessions"],
)
async def get_session(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetSessionResponse:
    return GetSessionResponse(
        session=await session_service.get_session(session_id, doctor.id)
    )


@router.get(
    "/sessions/{session_id}/patient-history",
    response_model=GetPatientHistoryResponse,
    response_model_exclude_none=True,
    tags=["sessions"],
)
async def get_patient_history(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetPatientHistoryResponse:
    return GetPatientHistoryResponse(
        sessions=await session_service.get_patient_history(session_id, doctor.id)
    )


@router.get(
    "/patients/profile-by-phone",
    response_model=GetPatientProfileByPhoneResponse,
    tags=["sessions"],
)
async def get_patient_profile_by_phone(
    phone: str = Query(...),
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetPatientProfileByPhoneResponse:
    profile = await session_service.get_patient_profile_by_phone(phone, doctor.id)
    return GetPatientProfileByPhoneResponse(found=profile is not None, profile=profile)


@router.patch(
    "/sessions/{session_id}/case-status",
    response_model=GetSessionResponse,
    response_model_exclude_none=True,
    tags=["sessions"],
)
async def update_case_status(
    session_id: str,
    request: UpdateCaseStatusRequest,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetSessionResponse:
    return GetSessionResponse(
        session=await session_service.update_case_status(
            session_id, doctor.id, request.caseStatus
        )
    )


@router.post(
    "/sessions/{session_id}/audio",
    response_model=UploadSessionAudioResponse,
    response_model_exclude_none=True,
    status_code=201,
    tags=["sessions"],
)
async def upload_session_audio(
    session_id: str,
    request: Request,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> UploadSessionAudioResponse:
    data = await request.body()
    session = await session_service.upload_audio(
        session_id,
        doctor.id,
        file_name=request.headers.get("x-audio-file-name"),
        mime_type=request.headers.get("content-type"),
        duration_seconds=float(
            request.headers.get("x-audio-duration-seconds", "0") or 0
        ),
        data=data,
        storage=LocalStorage(),
    )
    return UploadSessionAudioResponse(session=session)


@router.post(
    "/sessions/{session_id}/transcription",
    response_model=SubmitSessionTranscriptionResponse,
    response_model_exclude_none=True,
    status_code=202,
    tags=["sessions"],
)
async def submit_session_transcription(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
    x_idempotency_key: str | None = Header(default=None),
) -> SubmitSessionTranscriptionResponse:
    session = await session_service.request_transcription(
        session_id, doctor.id, x_idempotency_key
    )
    return SubmitSessionTranscriptionResponse(session=session)


@router.put(
    "/sessions/{session_id}/draft-document",
    response_model=UpdateDraftDocumentResponse,
    response_model_exclude_none=True,
    tags=["sessions"],
)
async def update_draft_document(
    session_id: str,
    draft_document: DraftConsultationDocument,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> UpdateDraftDocumentResponse:
    session = await session_service.update_draft_document(
        session_id, doctor.id, draft_document
    )
    return UpdateDraftDocumentResponse(session=session)


@router.post(
    "/sessions/{session_id}/pdf",
    response_model=GenerateSessionPdfResponse,
    response_model_exclude_none=True,
    tags=["sessions"],
)
async def generate_session_pdf(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GenerateSessionPdfResponse:
    session = await session_service.generate_session_pdf(
        session_id, doctor.id, LocalStorage()
    )
    return GenerateSessionPdfResponse(session=session)


@router.get("/sessions/{session_id}/artifacts/transcript", tags=["sessions"])
async def get_transcript_artifact(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    file_name, mime_type, data = await session_service.get_transcript_download(
        session_id, doctor.id
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/sessions/{session_id}/artifacts/audio", tags=["sessions"])
async def get_audio_artifact(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    file_name, mime_type, data = await session_service.get_audio_download(
        session_id, doctor.id, LocalStorage()
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/sessions/{session_id}/artifacts/pdf", tags=["sessions"])
async def get_pdf_artifact(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    file_name, mime_type, data = await session_service.get_pdf_download(
        session_id, doctor.id, LocalStorage()
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/sessions/{session_id}/report-context/uploads",
    response_model=UploadReportContextFileResponse,
    status_code=201,
    tags=["report-context"],
)
async def upload_report_context_file(
    session_id: str,
    request: Request,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> UploadReportContextFileResponse:
    upload = await session_service.upload_report_context_file(
        session_id,
        doctor.id,
        file_name=request.headers.get("x-report-file-name"),
        mime_type=request.headers.get("content-type"),
        data=await request.body(),
        storage=LocalStorage(),
    )
    return UploadReportContextFileResponse(upload=upload)


@router.get(
    "/sessions/{session_id}/report-context/uploads",
    response_model=ListReportContextUploadsResponse,
    tags=["report-context"],
)
async def list_report_context_uploads(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> ListReportContextUploadsResponse:
    uploads = await session_service.list_report_context_uploads(session_id, doctor.id)
    return ListReportContextUploadsResponse(uploads=uploads)


@router.delete(
    "/sessions/{session_id}/report-context/uploads/{upload_id}",
    status_code=204,
    tags=["report-context"],
)
async def delete_report_context_upload(
    session_id: str,
    upload_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    await session_service.delete_report_context_upload(session_id, upload_id, doctor.id)
    return Response(status_code=204)


@router.get(
    "/sessions/{session_id}/report-context/uploads/{upload_id}/content",
    tags=["report-context"],
)
async def get_report_context_upload_content(
    session_id: str,
    upload_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    file_name, mime_type, data = (
        await session_service.get_report_context_upload_content(
            session_id,
            upload_id,
            doctor.id,
            LocalStorage(),
        )
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_name}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post(
    "/sessions/{session_id}/report-context/extract",
    response_model=ExtractReportContextResponse,
    tags=["report-context"],
)
async def extract_report_context(
    session_id: str,
    request: ExtractReportContextRequest,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> ExtractReportContextResponse:
    session = await session_service.extract_report_context(
        session_id, request.uploadIds, doctor.id
    )
    return ExtractReportContextResponse(session=session)


@router.post(
    "/sessions/{session_id}/report-context/extract-async",
    response_model=CreateReportContextExtractionJobResponse,
    response_model_exclude_none=True,
    status_code=202,
    tags=["report-context"],
)
async def start_report_context_extraction_job(
    session_id: str,
    request: ExtractReportContextRequest,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> CreateReportContextExtractionJobResponse:
    job = await session_service.start_report_context_extraction_job(
        session_id, request.uploadIds, doctor.id
    )
    return CreateReportContextExtractionJobResponse(job=job)


@router.get(
    "/sessions/{session_id}/report-context/jobs/active",
    response_model=GetActiveReportContextExtractionJobResponse,
    tags=["report-context"],
)
async def get_active_report_context_extraction_job(
    session_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetActiveReportContextExtractionJobResponse:
    job = await session_service.get_active_report_context_extraction_job(
        session_id, doctor.id
    )
    return GetActiveReportContextExtractionJobResponse(job=job)


@router.get(
    "/sessions/{session_id}/report-context/jobs/{job_id}",
    response_model=GetReportContextExtractionJobResponse,
    response_model_exclude_none=True,
    tags=["report-context"],
)
async def get_report_context_extraction_job(
    session_id: str,
    job_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetReportContextExtractionJobResponse:
    job = await session_service.get_report_context_extraction_job(
        session_id, job_id, doctor.id
    )
    return GetReportContextExtractionJobResponse(job=job)


@router.get(
    "/sessions/{session_id}/report-context/jobs/{job_id}/stream",
    tags=["report-context"],
)
async def stream_report_context_extraction_job(
    session_id: str,
    job_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    job = await session_service.get_report_context_extraction_job(
        session_id, job_id, doctor.id
    )
    payload = (
        f"retry: 2000\n\nevent: job\ndata: "
        f"{job.model_dump_json(exclude_none=True)}\n\n"
    )
    return Response(
        content=payload,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform"},
    )


@router.post(
    "/sessions/{session_id}/report-context/jobs/{job_id}/cancel",
    response_model=GetReportContextExtractionJobResponse,
    response_model_exclude_none=True,
    tags=["report-context"],
)
async def cancel_report_context_extraction_job(
    session_id: str,
    job_id: str,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetReportContextExtractionJobResponse:
    job = await session_service.cancel_report_context_extraction_job(
        session_id, job_id, doctor.id
    )
    return GetReportContextExtractionJobResponse(job=job)


@router.post(
    "/sessions/{session_id}/report-context/suggestions", tags=["report-context"]
)
async def suggest_report_context_draft(
    session_id: str,
    request: Request,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> dict:
    body = await request.json()
    return await session_service.suggest_report_context_draft(
        session_id,
        body.get("chunkIds", []),
        body.get("customChunks", []),
        doctor.id,
    )


@router.post(
    "/sessions/{session_id}/report-context/apply",
    response_model=GetSessionResponse,
    tags=["report-context"],
)
async def apply_report_context_draft(
    session_id: str,
    request: Request,
    doctor=Depends(require_doctor),
    session_service: SessionService = Depends(get_session_service),
) -> GetSessionResponse:
    body = await request.json()
    draft_document = DraftConsultationDocument.model_validate(
        body.get("draftDocument", {})
    )
    session = await session_service.apply_report_context_draft(
        session_id,
        body.get("chunkIds", []),
        body.get("customChunks", []),
        draft_document,
        doctor.id,
    )
    return GetSessionResponse(session=session)


@router.get(
    "/public/intake/{token}",
    response_model=GetPublicIntakeLinkResponse,
    response_model_exclude_none=True,
    tags=["public-intake"],
)
async def get_public_intake_link(
    token: str,
    queue_service: QueueService = Depends(get_queue_service),
) -> GetPublicIntakeLinkResponse:
    return GetPublicIntakeLinkResponse(
        intakeLink=await queue_service.get_public_intake_link(token)
    )


@router.get(
    "/public/intake/{token}/patient-profile",
    response_model=GetPatientProfileByPhoneResponse,
    response_model_exclude_none=True,
    tags=["public-intake"],
)
async def get_public_patient_profile(
    token: str,
    phone: str = Query(...),
    queue_service: QueueService = Depends(get_queue_service),
) -> GetPatientProfileByPhoneResponse:
    profile = await queue_service.get_public_patient_profile_by_phone(token, phone)
    return GetPatientProfileByPhoneResponse(found=profile is not None, profile=profile)


@router.get(
    "/public/intake/by-slug/{department_slug}/{doctor_name_slug}",
    response_model=GetPublicIntakeLinkResponse,
    response_model_exclude_none=True,
    tags=["public-intake"],
)
async def get_public_intake_link_by_slug(
    department_slug: str,
    doctor_name_slug: str,
    auth_service: AuthService = Depends(get_auth_service),
    queue_service: QueueService = Depends(get_queue_service),
) -> GetPublicIntakeLinkResponse:
    doctor = await auth_service.get_doctor_by_public_path(
        department_slug, doctor_name_slug
    )
    intake_link = await queue_service.get_doctor_intake_link(doctor)
    return GetPublicIntakeLinkResponse(intakeLink=intake_link)


@router.post(
    "/public/intake/{token}",
    response_model=SubmitPublicIntakeResponse,
    response_model_exclude_none=True,
    status_code=201,
    tags=["public-intake"],
)
async def submit_public_intake(
    token: str,
    request: SubmitPublicIntakeRequest,
    queue_service: QueueService = Depends(get_queue_service),
) -> SubmitPublicIntakeResponse:
    return SubmitPublicIntakeResponse(
        entry=await queue_service.submit_public_intake(token, request)
    )


@router.get(
    "/intake-link",
    response_model=GetDoctorIntakeLinkResponse,
    response_model_exclude_none=True,
    tags=["queue"],
)
async def get_doctor_intake_link(
    doctor=Depends(require_doctor),
    queue_service: QueueService = Depends(get_queue_service),
) -> GetDoctorIntakeLinkResponse:
    return GetDoctorIntakeLinkResponse(
        intakeLink=await queue_service.get_doctor_intake_link(doctor)
    )


@router.get(
    "/queue",
    response_model=QueueListResponse,
    response_model_exclude_none=True,
    tags=["queue"],
)
async def list_queue_entries(
    doctor=Depends(require_doctor),
    queue_service: QueueService = Depends(get_queue_service),
) -> QueueListResponse:
    return QueueListResponse(entries=await queue_service.list_queue_entries(doctor.id))


@router.post(
    "/queue/{queue_entry_id}/accept",
    response_model=AcceptQueueEntryResponse,
    response_model_exclude_none=True,
    status_code=201,
    tags=["queue"],
)
async def accept_queue_entry(
    queue_entry_id: str,
    doctor=Depends(require_doctor),
    queue_service: QueueService = Depends(get_queue_service),
) -> AcceptQueueEntryResponse:
    entry, session = await queue_service.accept_queue_entry(queue_entry_id, doctor)
    return AcceptQueueEntryResponse(entry=entry, session=session)


@router.post(
    "/queue/{queue_entry_id}/no-show",
    response_model=UpdateQueueEntryResponse,
    response_model_exclude_none=True,
    tags=["queue"],
)
async def mark_queue_entry_no_show(
    queue_entry_id: str,
    doctor=Depends(require_doctor),
    queue_service: QueueService = Depends(get_queue_service),
) -> UpdateQueueEntryResponse:
    return UpdateQueueEntryResponse(
        entry=await queue_service.mark_queue_entry_no_show(queue_entry_id, doctor)
    )


@router.post(
    "/queue/{queue_entry_id}/cancel",
    response_model=UpdateQueueEntryResponse,
    response_model_exclude_none=True,
    tags=["queue"],
)
async def cancel_queue_entry(
    queue_entry_id: str,
    doctor=Depends(require_doctor),
    queue_service: QueueService = Depends(get_queue_service),
) -> UpdateQueueEntryResponse:
    return UpdateQueueEntryResponse(
        entry=await queue_service.cancel_queue_entry(queue_entry_id, doctor)
    )


@router.get("/internal/admin/doctors", tags=["internal-admin"])
async def admin_list_doctors(
    _admin=Depends(require_internal_admin),
    search: str | None = Query(default=None),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    return {"doctors": await auth_service.list_doctor_profiles(search)}


@router.get("/internal/admin/doctors/{doctor_id}", tags=["internal-admin"])
async def admin_get_doctor(
    doctor_id: str,
    _admin=Depends(require_internal_admin),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    return {"doctor": await auth_service.get_doctor_by_id(doctor_id)}


@router.post("/internal/admin/doctors", status_code=201, tags=["internal-admin"])
async def admin_create_doctor(
    request: Request,
    _admin=Depends(require_internal_admin),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    body = await request.json()
    doctor = await auth_service.create_doctor(
        name=body["name"],
        department=body.get("department"),
        phone_number=body.get("phoneNumber"),
        email=body.get("email"),
    )
    return {"doctor": doctor}


@router.put("/internal/admin/doctors/{doctor_id}", tags=["internal-admin"])
async def admin_update_doctor(
    doctor_id: str,
    request: Request,
    _admin=Depends(require_internal_admin),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    body = await request.json()
    doctor = await auth_service.update_doctor_by_id(
        doctor_id,
        name=body["name"],
        department=body.get("department"),
        phone_number=body.get("phoneNumber"),
        email=body.get("email"),
    )
    return {"doctor": doctor}


@router.post("/internal/admin/doctors/{doctor_id}/pin-reset", tags=["internal-admin"])
async def admin_reset_doctor_pin(
    doctor_id: str,
    _admin=Depends(require_internal_admin),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    return await auth_service.reset_doctor_pin(doctor_id)


@router.post(
    "/internal/admin/doctors/{doctor_id}/pin-lock/clear", tags=["internal-admin"]
)
async def admin_clear_doctor_pin_lock(
    doctor_id: str,
    _admin=Depends(require_internal_admin),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    return {"doctor": await auth_service.clear_doctor_pin_lock(doctor_id)}


@router.get("/internal/admin/intake-links", tags=["internal-admin"])
async def admin_list_intake_links(
    _admin=Depends(require_internal_admin),
    doctorId: str | None = Query(default=None),
    queue_service: QueueService = Depends(get_queue_service),
) -> dict[str, object]:
    return {"intakeLinks": await queue_service.list_intake_links_for_admin(doctorId)}


@router.get("/internal/admin/intake-links/{intake_link_id}", tags=["internal-admin"])
async def admin_get_intake_link(
    intake_link_id: str,
    _admin=Depends(require_internal_admin),
    queue_service: QueueService = Depends(get_queue_service),
) -> dict[str, object]:
    return {
        "intakeLink": await queue_service.get_intake_link_by_id_for_admin(
            intake_link_id
        )
    }


@router.patch("/internal/admin/intake-links/{intake_link_id}", tags=["internal-admin"])
async def admin_update_intake_link(
    intake_link_id: str,
    request: Request,
    _admin=Depends(require_internal_admin),
    queue_service: QueueService = Depends(get_queue_service),
) -> dict[str, object]:
    body = await request.json()
    return {
        "intakeLink": await queue_service.update_intake_link_for_admin(
            intake_link_id,
            label=body.get("label"),
            is_active=body.get("isActive"),
        )
    }


@router.get("/internal/admin/queue", tags=["internal-admin"])
async def admin_list_queue_entries(
    _admin=Depends(require_internal_admin),
    doctorId: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    queue_service: QueueService = Depends(get_queue_service),
) -> dict[str, object]:
    return {
        "entries": await queue_service.list_queue_entries_admin(
            doctor_id=doctorId,
            status=status,
            search=search,
        )
    }


@router.get("/internal/admin/queue/{queue_entry_id}", tags=["internal-admin"])
async def admin_get_queue_entry(
    queue_entry_id: str,
    _admin=Depends(require_internal_admin),
    queue_service: QueueService = Depends(get_queue_service),
) -> dict[str, object]:
    return {
        "entry": await queue_service.get_queue_entry_by_id_for_admin(queue_entry_id)
    }


@router.post(
    "/internal/admin/queue/{queue_entry_id}/accept",
    status_code=201,
    tags=["internal-admin"],
)
async def admin_accept_queue_entry(
    queue_entry_id: str,
    _admin=Depends(require_internal_admin),
    queue_service: QueueService = Depends(get_queue_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    entry = await queue_service.get_queue_entry_by_id_for_admin(queue_entry_id)
    doctor = await auth_service.get_doctor_by_id(entry.doctorId)
    accepted_entry, session = await queue_service.accept_queue_entry(
        queue_entry_id, doctor
    )
    return {"entry": accepted_entry, "session": session}


@router.post("/internal/admin/queue/{queue_entry_id}/no-show", tags=["internal-admin"])
async def admin_mark_queue_entry_no_show(
    queue_entry_id: str,
    _admin=Depends(require_internal_admin),
    queue_service: QueueService = Depends(get_queue_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    entry = await queue_service.get_queue_entry_by_id_for_admin(queue_entry_id)
    doctor = await auth_service.get_doctor_by_id(entry.doctorId)
    return {
        "entry": await queue_service.mark_queue_entry_no_show(queue_entry_id, doctor)
    }


@router.post("/internal/admin/queue/{queue_entry_id}/cancel", tags=["internal-admin"])
async def admin_cancel_queue_entry(
    queue_entry_id: str,
    _admin=Depends(require_internal_admin),
    queue_service: QueueService = Depends(get_queue_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, object]:
    entry = await queue_service.get_queue_entry_by_id_for_admin(queue_entry_id)
    doctor = await auth_service.get_doctor_by_id(entry.doctorId)
    return {"entry": await queue_service.cancel_queue_entry(queue_entry_id, doctor)}


@router.get("/internal/admin/sessions", tags=["internal-admin"])
async def admin_list_sessions(
    _admin=Depends(require_internal_admin),
    search: str | None = Query(default=None),
    doctorId: str | None = Query(default=None),
    transcriptionStatus: str | None = Query(default=None),
    hasDocument: bool | None = Query(default=None),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    sessions = await session_service.list_sessions_admin(doctorId, search)
    if transcriptionStatus is not None:
        sessions = [
            session
            for session in sessions
            if session.transcription
            and session.transcription.status == transcriptionStatus
        ]
    if hasDocument is not None:
        sessions = [
            session
            for session in sessions
            if (session.documentArtifact is not None) is hasDocument
        ]
    return {"sessions": sessions}


@router.post("/internal/admin/sessions", status_code=201, tags=["internal-admin"])
async def admin_create_session(
    request: Request,
    _admin=Depends(require_internal_admin),
    auth_service: AuthService = Depends(get_auth_service),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    body = await request.json()
    doctor = await auth_service.get_doctor_by_id(body["doctorId"])
    session = await session_service.create_session(
        doctor.id,
        CreateSessionRequest.model_validate(
            {
                "patientName": body["patient"]["name"],
                "patientPhoneNumber": body["patient"]["phoneNumber"],
                "patientAge": body["patient"].get("age"),
            }
        ),
    )
    return {"session": session}


@router.get("/internal/admin/sessions/{session_id}", tags=["internal-admin"])
async def admin_get_session(
    session_id: str,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    return {"session": await session_service.get_session_any(session_id)}


@router.get(
    "/internal/admin/sessions/{session_id}/transcription", tags=["internal-admin"]
)
async def admin_get_session_transcription(
    session_id: str,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    session = await session_service.get_session_any(session_id)
    return {"transcription": session.transcription}


@router.put(
    "/internal/admin/sessions/{session_id}/draft-document", tags=["internal-admin"]
)
async def admin_update_draft_document(
    session_id: str,
    request: DraftConsultationDocument,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    session = await session_service.get_session_any(session_id)
    updated = await session_service.update_draft_document(
        session_id, session.doctorId, request
    )
    return {"session": updated}


@router.post(
    "/internal/admin/sessions/{session_id}/transcription",
    status_code=202,
    tags=["internal-admin"],
)
async def admin_request_transcription(
    session_id: str,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
    x_idempotency_key: str | None = Header(default=None),
) -> dict[str, object]:
    session = await session_service.request_transcription_admin(
        session_id, x_idempotency_key
    )
    return {"session": session}


@router.post("/internal/admin/sessions/{session_id}/pdf", tags=["internal-admin"])
async def admin_generate_pdf(
    session_id: str,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    session = await session_service.generate_session_pdf_admin(
        session_id, LocalStorage()
    )
    return {"session": session}


@router.get(
    "/internal/admin/sessions/{session_id}/artifacts/transcript",
    tags=["internal-admin"],
)
async def admin_get_transcript_artifact(
    session_id: str,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    file_name, mime_type, data = await session_service.get_transcript_download_admin(
        session_id
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get(
    "/internal/admin/sessions/{session_id}/artifacts/audio", tags=["internal-admin"]
)
async def admin_get_audio_artifact(
    session_id: str,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    file_name, mime_type, data = await session_service.get_audio_download_admin(
        session_id, LocalStorage()
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get(
    "/internal/admin/sessions/{session_id}/artifacts/pdf", tags=["internal-admin"]
)
async def admin_get_pdf_artifact(
    session_id: str,
    _admin=Depends(require_internal_admin),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    file_name, mime_type, data = await session_service.get_pdf_download_admin(
        session_id, LocalStorage()
    )
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/internal/admin/fettle-sync/stats", tags=["internal-admin"])
async def admin_fettle_sync_stats(
    _admin=Depends(require_internal_admin),
    fettle_sync_service: FettleSyncService = Depends(get_fettle_sync_service),
) -> dict[str, object]:
    return await fettle_sync_service.get_stats()


@router.get("/internal/admin/fettle-sync/jobs", tags=["internal-admin"])
async def admin_fettle_sync_jobs(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    _admin=Depends(require_internal_admin),
    fettle_sync_service: FettleSyncService = Depends(get_fettle_sync_service),
) -> dict[str, object]:
    return {"jobs": await fettle_sync_service.list_jobs(status=status, limit=limit)}


@router.post(
    "/internal/admin/fettle-sync/dry-run",
    status_code=202,
    response_model=None,
    tags=["internal-admin"],
)
async def admin_fettle_sync_dry_run(
    _admin=Depends(require_internal_admin),
    fettle_sync_service: FettleSyncService = Depends(get_fettle_sync_service),
) -> dict[str, object] | Response:
    if not fettle_sync_service.enabled:
        return _json_error_response(409, "Fettle sync is not configured.")
    job = await fettle_sync_service.enqueue_dry_run()
    result = await fettle_sync_service.process_due_jobs(limit=1)
    return {"job": job, "result": result}


@router.post(
    "/internal/admin/fettle-sync/jobs/{job_id}/replay",
    response_model=None,
    tags=["internal-admin"],
)
async def admin_fettle_sync_replay(
    job_id: str,
    _admin=Depends(require_internal_admin),
    fettle_sync_service: FettleSyncService = Depends(get_fettle_sync_service),
) -> dict[str, object] | Response:
    if not fettle_sync_service.enabled:
        return _json_error_response(409, "Fettle sync is not configured.")
    job = await fettle_sync_service.replay_dead_letter(job_id)
    if job is None:
        return _json_error_response(404, "Dead-letter sync job was not found.")
    return {"job": job}


@router.get("/public/reliability-kpis", tags=["kpi"])
async def public_reliability_kpis(
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return await observability_service.get_reliability_summary()


@router.get("/public/kpi/ops-summary", tags=["kpi"])
async def public_kpi_ops_summary(
    window: str | None = Query(default=None),
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return await observability_service.get_ops_summary(window=window or "last_24h")


@router.get("/public/kpi/scorecard", tags=["kpi"])
async def public_kpi_scorecard(
    focus: str | None = Query(default=None),
    includeExpected: str | None = Query(default=None),
    today: str | None = Query(default=None),
    window: str | None = Query(default=None),
    section: str | None = Query(default=None),
    rating: str | None = Query(default=None),
    metricContains: str | None = Query(default=None),
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    focus_mode = str(focus or "").strip().lower() in {"1", "true", "yes"}
    include_expected = str(includeExpected or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    filter_today = str(today or "").strip().lower() in {"1", "true", "yes"}
    selected_window = window or ("today" if filter_today else None)
    return await observability_service.get_scorecard(
        focus=focus_mode,
        include_expected=include_expected,
        window=selected_window,
        section=section,
        rating=rating,
        metric_contains=metricContains,
    )


@router.get("/public/kpi/providers", tags=["kpi"])
async def public_kpi_providers(
    window: str | None = Query(default=None),
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return await observability_service.get_provider_diagnostics(
        window=window or "today"
    )


@router.get("/public/kpi/pipeline", tags=["kpi"])
async def public_kpi_pipeline(
    window: str | None = Query(default=None),
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return await observability_service.get_pipeline_diagnostics(
        window=window or "today"
    )


@router.get("/public/kpi/report-context-fallback", tags=["kpi"])
async def public_kpi_report_context_fallback(
    window: str | None = Query(default=None),
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return await observability_service.get_report_context_fallback_diagnostics(
        window=window or "today"
    )


@router.get("/public/kpi/event-trends", tags=["kpi"])
async def public_kpi_event_trends(
    window: str | None = Query(default=None),
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return await observability_service.get_event_trends(window=window or "today")


@router.get("/internal/admin/reliability/alerts", tags=["internal-admin"])
async def admin_reliability_alerts(
    _admin=Depends(require_internal_admin),
    observability_service: ObservabilityService = Depends(get_observability_service),
) -> dict:
    return await observability_service.get_reliability_alerts()


@router.put("/sessions/{session_id}/pdf-delivery", tags=["sessions"])
async def prepare_pdf_delivery(
    session_id: str,
    request: Request,
    doctor=Depends(require_doctor),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object]:
    delivery = await pdf_delivery_service.prepare_session_delivery(
        session_id, await request.json(), doctor.id
    )
    return {"delivery": delivery}


@router.get("/sessions/{session_id}/pdf-delivery", tags=["sessions"])
async def get_pdf_delivery_status(
    session_id: str,
    doctor=Depends(require_doctor),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object]:
    delivery = await pdf_delivery_service.get_session_delivery_status(
        session_id, doctor.id
    )
    return {"delivery": delivery}


@router.post("/sessions/{session_id}/pdf-delivery/send", tags=["sessions"])
async def send_pdf_delivery(
    session_id: str,
    request: Request,
    doctor=Depends(require_doctor),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object]:
    delivery = await pdf_delivery_service.send_session_delivery(
        session_id, await request.json(), doctor.id
    )
    return {"delivery": delivery}


@router.put(
    "/internal/admin/sessions/{session_id}/pdf-delivery", tags=["internal-admin"]
)
async def admin_prepare_pdf_delivery(
    session_id: str,
    request: Request,
    _admin=Depends(require_internal_admin),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object]:
    delivery = await pdf_delivery_service.prepare_session_delivery(
        session_id, await request.json()
    )
    return {"delivery": delivery}


@router.get(
    "/internal/admin/sessions/{session_id}/pdf-delivery", tags=["internal-admin"]
)
async def admin_get_pdf_delivery_status(
    session_id: str,
    _admin=Depends(require_internal_admin),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object]:
    delivery = await pdf_delivery_service.get_session_delivery_status(session_id)
    return {"delivery": delivery}


@router.post(
    "/internal/admin/sessions/{session_id}/pdf-delivery/send", tags=["internal-admin"]
)
async def admin_send_pdf_delivery(
    session_id: str,
    request: Request,
    _admin=Depends(require_internal_admin),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object]:
    delivery = await pdf_delivery_service.send_session_delivery(
        session_id, await request.json()
    )
    return {"delivery": delivery}


@router.get("/internal/admin/pdf-delivery/jobs", tags=["internal-admin"])
async def admin_pdf_delivery_jobs(
    _admin=Depends(require_internal_admin),
    sessionId: str | None = Query(default=None),
    status: str | None = Query(default=None),
    recipientType: str | None = Query(default=None),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object]:
    return {
        "jobs": await pdf_delivery_service.list_jobs_for_admin(
            session_id=sessionId,
            status=status,
            recipient_type=recipientType,
        )
    }


@router.get(
    "/internal/admin/pdf-delivery/jobs/{job_id}",
    response_model=None,
    tags=["internal-admin"],
)
async def admin_pdf_delivery_job(
    job_id: str,
    _admin=Depends(require_internal_admin),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> dict[str, object] | Response:
    job = await pdf_delivery_service.get_job_by_id_for_admin(job_id)
    if job is None:
        return Response(
            status_code=404,
            content='{"error":"PDF delivery job was not found."}',
            media_type="application/json",
        )
    return {"job": job}


@router.post("/webhooks/twilio/whatsapp-status", tags=["pdf-delivery"])
async def twilio_whatsapp_status_webhook(
    request: Request,
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> Response:
    form = parse_qs((await request.body()).decode())
    message_sid = str(form.get("MessageSid", [""])[0]).strip()
    if message_sid:
        await pdf_delivery_service.handle_provider_status_update(
            provider_message_id=message_sid,
            provider_status=str(form.get("MessageStatus", [""])[0]).strip() or None,
            error_message=str(form.get("ErrorMessage", [""])[0]).strip() or None,
        )
    return Response(status_code=204)


@router.get("/public/pdf-delivery/template-sample.pdf", tags=["pdf-delivery"])
async def public_pdf_delivery_template_sample() -> Response:
    return Response(
        content=SAMPLE_PDF_BYTES,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="medivoice-template-sample.pdf"',
            "Content-Length": str(len(SAMPLE_PDF_BYTES)),
            "Cache-Control": "public, max-age=300",
        },
    )


@router.get("/public/pdf-delivery/media", tags=["pdf-delivery"])
@router.get("/public/pdf-delivery/media/{file_name}", tags=["pdf-delivery"])
async def public_pdf_delivery_media(
    file_name: str | None = None,
    token: str | None = Query(default=None),
    pdf_delivery_service: PdfDeliveryService = Depends(get_pdf_delivery_service),
) -> Response:
    if not token:
        return Response(status_code=404)
    asset = await pdf_delivery_service.get_public_media_asset(token)
    if asset is None:
        return Response(status_code=404)
    asset_file_name, mime_type, data = asset
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{asset_file_name.replace(chr(34), "")}"',
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store",
        },
    )
