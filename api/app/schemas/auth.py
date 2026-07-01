from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class LoginRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else None

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else None

    @model_validator(mode="after")
    def require_identifier(self) -> "LoginRequest":
        if not self.email and not self.username:
            raise ValueError("email or username is required")
        return self


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
