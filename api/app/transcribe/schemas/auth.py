from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from api.app.transcribe.schemas.common import DoctorProfile, PublicDoctorProfile


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
