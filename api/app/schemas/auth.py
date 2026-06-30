from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from api.app.schemas.hospital.transcribe import DoctorProfile, PublicDoctorProfile


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, v: str) -> str:
        return v.strip().lower()


class TokenResponse(BaseModel):
    error: int = 0
    msg: str = "Login successful"
    token: str
    role: str
    name: str
    organization_id: str
    user_id: str
    permissions: dict[str, bool] | None = None


class ValidateTokenResponse(BaseModel):
    error: int = 0
    msg: str = "Success"
    role: str
    name: str
    email: str
    user_id: str
    organization_id: str


class TabAccessResponse(BaseModel):
    error: int = 0
    permissions: dict[str, bool]


class ErrorResponse(BaseModel):
    error: int = 1
    msg: str
    errorMsg: str | None = None


class ListDoctorsResponse(BaseModel):
    doctors: list[PublicDoctorProfile]


class SelectDoctorSessionRequest(BaseModel):
    doctorId: str


class LoginDoctorResponse(BaseModel):
    token: str
    doctor: DoctorProfile


class PinLoginRequest(BaseModel):
    doctorId: str
    pin: str = Field(min_length=6, max_length=6)


class PinChangeRequest(BaseModel):
    pinChangeToken: str
    newPin: str = Field(min_length=6, max_length=6)


class PinLoginAuthenticatedResponse(BaseModel):
    status: Literal["authenticated"]
    token: str
    doctor: DoctorProfile


class PinLoginPinChangeRequiredResponse(BaseModel):
    status: Literal["pin_change_required"]
    pinChangeToken: str
    doctor: DoctorProfile


class AuthMeResponse(BaseModel):
    doctor: DoctorProfile
