from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException

from api.app.schemas.hospital.transcribe import (
    ConsultationSession,
    DoctorProfile,
    ErrorResponse,
    IntakeLink,
    PatientProfile,
    QueueEntry,
    SubmitPublicIntakeRequest,
)


class QueueService:
    def __init__(self, repository, session_repository) -> None:
        self.repository = repository
        self.session_repository = session_repository

    async def get_doctor_intake_link(self, doctor: DoctorProfile) -> IntakeLink:
        intake_link = await self.repository.get_or_create_default_intake_link(
            doctor.id, doctor.name
        )
        if intake_link is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Doctor account not found.").model_dump(),
            )
        return intake_link

    async def get_public_intake_link(self, token: str) -> IntakeLink:
        intake_link = await self.repository.get_public_intake_link_by_token(
            token.strip()
        )
        if intake_link is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="This intake link is not available."
                ).model_dump(),
            )
        return intake_link

    async def submit_public_intake(
        self, token: str, request: SubmitPublicIntakeRequest
    ) -> QueueEntry:
        intake_link = await self.get_public_intake_link(token)
        existing = await self.repository.find_recent_waiting_entry_by_phone(
            intake_link.doctorId,
            request.patient.phoneNumber,
        )
        if existing is not None:
            submitted_at = existing.submittedAt
            if (datetime.now(UTC) - submitted_at).total_seconds() <= 300:
                return existing
        return await self.repository.create_queue_entry(
            doctor_id=intake_link.doctorId,
            intake_link_id=intake_link.id,
            intake_label=intake_link.label,
            patient_name=request.patient.name,
            patient_phone_number=request.patient.phoneNumber,
            patient_age=request.patient.age,
            source="qr",
        )

    async def get_public_patient_profile_by_phone(
        self, token: str, phone_number: str
    ) -> PatientProfile | None:
        intake_link = await self.get_public_intake_link(token)
        return await self.session_repository.get_patient_profile_by_phone(
            phone_number, intake_link.doctorId
        )

    async def list_queue_entries(self, doctor_id: str) -> list[QueueEntry]:
        return await self.repository.list_queue_entries(doctor_id)

    async def list_queue_entries_admin(
        self,
        *,
        doctor_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list[QueueEntry]:
        return await self.repository.list_queue_entries_admin(
            doctor_id=doctor_id,
            status=status,
            search=search,
        )

    async def get_queue_entry_by_id_for_admin(self, queue_entry_id: str) -> QueueEntry:
        entry = await self.repository.get_queue_entry_by_id(queue_entry_id)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="The waiting patient could not be found."
                ).model_dump(),
            )
        return entry

    async def list_intake_links_for_admin(
        self, doctor_id: str | None = None
    ) -> list[IntakeLink]:
        return await self.repository.list_intake_links(doctor_id)

    async def get_intake_link_by_id_for_admin(self, intake_link_id: str) -> IntakeLink:
        intake_link = await self.repository.get_intake_link_by_id(intake_link_id)
        if intake_link is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Intake link not found.").model_dump(),
            )
        return intake_link

    async def update_intake_link_for_admin(
        self,
        intake_link_id: str,
        *,
        label: str | None,
        is_active: bool | None,
    ) -> IntakeLink:
        intake_link = await self.repository.update_intake_link(
            intake_link_id,
            label=label,
            is_active=is_active,
        )
        if intake_link is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Intake link not found.").model_dump(),
            )
        return intake_link

    async def accept_queue_entry(
        self, queue_entry_id: str, doctor: DoctorProfile
    ) -> tuple[QueueEntry, ConsultationSession]:
        entry = await self.repository.get_queue_entry_by_id(queue_entry_id)
        if entry is None or entry.doctorId != doctor.id:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="The waiting patient could not be found."
                ).model_dump(),
            )
        if entry.status == "accepted" and entry.sessionId:
            session = await self.session_repository.get_session(
                entry.sessionId, doctor.id
            )
            if session is None:
                raise HTTPException(
                    status_code=409,
                    detail=ErrorResponse(
                        message="Accepted queue entry is missing its session."
                    ).model_dump(),
                )
            return entry, session
        if entry.status != "waiting":
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="This patient is no longer waiting in the queue."
                ).model_dump(),
            )
        session = await self.session_repository.create_session(
            doctor.id,
            entry.patient.name,
            entry.patient.phoneNumber,
            entry.patient.age,
        )
        updated_entry = await self.repository.accept_queue_entry(
            queue_entry_id, session.id
        )
        if updated_entry is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="The waiting patient could not be found."
                ).model_dump(),
            )
        return updated_entry, session

    async def mark_queue_entry_no_show(
        self, queue_entry_id: str, doctor: DoctorProfile
    ) -> QueueEntry:
        return await self._update_waiting_queue_entry_status(
            queue_entry_id, doctor, "no_show"
        )

    async def cancel_queue_entry(
        self, queue_entry_id: str, doctor: DoctorProfile
    ) -> QueueEntry:
        return await self._update_waiting_queue_entry_status(
            queue_entry_id, doctor, "cancelled"
        )

    async def _update_waiting_queue_entry_status(
        self, queue_entry_id: str, doctor: DoctorProfile, status: str
    ) -> QueueEntry:
        entry = await self.repository.get_queue_entry_by_id(queue_entry_id)
        if entry is None or entry.doctorId != doctor.id:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="The waiting patient could not be found."
                ).model_dump(),
            )
        if entry.status != "waiting":
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="This patient is no longer waiting in the queue."
                ).model_dump(),
            )
        updated = await self.repository.update_queue_entry_status(
            queue_entry_id, status
        )
        if updated is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="This patient is no longer waiting in the queue."
                ).model_dump(),
            )
        return updated
