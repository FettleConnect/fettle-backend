from __future__ import annotations

from fastapi import HTTPException, status

from api.app.core.config import Settings
from api.app.transcribe.schemas.auth import (
    AuthMeResponse,
    ListDoctorsResponse,
    LoginDoctorResponse,
    PinLoginAuthenticatedResponse,
    PinLoginPinChangeRequiredResponse,
)
from api.app.transcribe.schemas.common import DoctorProfile, ErrorResponse
from api.app.transcribe.services.tokens import (
    issue_legacy_doctor_token,
    issue_pin_change_token,
    verify_legacy_doctor_token,
    verify_pin_change_token,
)


class AuthService:
    def __init__(self, repository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    async def list_doctors(self, search: str | None = None) -> ListDoctorsResponse:
        return ListDoctorsResponse(doctors=await self.repository.list_doctors(search))

    async def list_doctor_profiles(self, search: str | None = None) -> list[DoctorProfile]:
        return await self.repository.list_doctor_profiles(search)

    async def select_doctor(self, doctor_id: str) -> LoginDoctorResponse:
        doctor = await self.repository.find_by_id(doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Doctor account not found.").model_dump())
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(message="PIN login is required before sign in.", code="PIN_LOGIN_REQUIRED").model_dump(),
        )

    async def login_with_pin(self, doctor_id: str, pin: str) -> PinLoginAuthenticatedResponse | PinLoginPinChangeRequiredResponse:
        doctor = await self.repository.find_by_id(doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Doctor account not found.").model_dump())
        if pin != doctor.pin:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorResponse(message="PIN did not match this doctor account.", code="INVALID_PIN").model_dump(),
            )
        doctor = await self.repository.touch_last_login(doctor_id) or doctor
        if doctor.must_change_pin:
            return PinLoginPinChangeRequiredResponse(
                status="pin_change_required",
                pinChangeToken=issue_pin_change_token(
                    doctor.profile.id,
                    doctor.token_version,
                    self.settings.legacy_auth_secret,
                ),
                doctor=doctor.profile,
            )
        token = issue_legacy_doctor_token(doctor.profile.id, doctor.token_version, self.settings.legacy_auth_secret)
        return PinLoginAuthenticatedResponse(status="authenticated", token=token, doctor=doctor.profile)

    async def current_doctor(self, token: str) -> AuthMeResponse:
        payload = verify_legacy_doctor_token(token, self.settings.legacy_auth_secret)
        if not payload:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Your session has expired. Please log in again.").model_dump(),
            )
        doctor_id = str(payload.get("doctorId", ""))
        doctor = await self.repository.find_by_id(doctor_id)
        if not doctor:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Your session has expired. Please log in again.").model_dump(),
            )
        if payload.get("tokenVersion") != doctor.token_version:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(
                    message="Your session has expired. Please log in again."
                ).model_dump(),
            )
        return AuthMeResponse(doctor=doctor.profile)

    async def get_doctor_by_public_path(self, department_slug: str, doctor_name_slug: str) -> DoctorProfile:
        doctor = await self.repository.find_by_public_path(department_slug, doctor_name_slug)
        if not doctor:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Doctor account not found.").model_dump())
        return doctor.profile

    async def get_doctor_by_id(self, doctor_id: str) -> DoctorProfile:
        doctor = await self.repository.get_profile_by_id(doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Doctor account not found.").model_dump())
        return doctor

    async def create_doctor(
        self,
        *,
        name: str,
        department: str | None,
        phone_number: str | None,
        email: str | None,
    ) -> DoctorProfile:
        return await self.repository.create_doctor(
            name=name,
            department=department,
            phone_number=phone_number,
            email=email,
        )

    async def update_doctor_by_id(
        self,
        doctor_id: str,
        *,
        name: str,
        department: str | None,
        phone_number: str | None,
        email: str | None,
    ) -> DoctorProfile:
        doctor = await self.repository.update_doctor(
            doctor_id,
            name=name,
            department=department,
            phone_number=phone_number,
            email=email,
        )
        if not doctor:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Doctor account not found.").model_dump())
        return doctor

    async def reset_doctor_pin(self, doctor_id: str) -> dict[str, object]:
        temporary_pin = "654321"
        doctor = await self.repository.reset_pin(doctor_id, temporary_pin)
        if not doctor:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Doctor account not found.").model_dump())
        return {"doctor": doctor, "temporaryPin": temporary_pin}

    async def clear_doctor_pin_lock(self, doctor_id: str) -> DoctorProfile:
        doctor = await self.repository.clear_pin_lock(doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Doctor account not found.").model_dump())
        return doctor

    async def change_pin(self, pin_change_token: str, new_pin: str) -> LoginDoctorResponse:
        payload = verify_pin_change_token(
            pin_change_token, self.settings.legacy_auth_secret
        )
        if not payload:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(message="Invalid PIN change token.").model_dump(),
            )
        doctor_id = str(payload.get("doctorId", ""))
        doctor_record = await self.repository.find_by_id(doctor_id)
        if doctor_record is None or payload.get("tokenVersion") != doctor_record.token_version:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(message="Invalid PIN change token.").model_dump(),
            )
        doctor = await self.repository.set_pin(doctor_id, new_pin)
        if not doctor:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(message="Doctor account not found.").model_dump(),
            )
        refreshed = await self.repository.find_by_id(doctor_id)
        token_version = refreshed.token_version if refreshed is not None else 0
        token = issue_legacy_doctor_token(
            doctor.id, token_version, self.settings.legacy_auth_secret
        )
        return LoginDoctorResponse(token=token, doctor=doctor)
