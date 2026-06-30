from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AdminLoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class AdminTokenResponse(BaseModel):
    token: str


class OrganizationAdminIn(BaseModel):
    name: str
    slug: str
    type: str = Field(pattern="^(hospital|corporate|clinical)$")
    subdomain: str | None = None
    is_active: bool = True

    @field_validator("slug", "subdomain", mode="before")
    @classmethod
    def normalize_slug(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else None


class OrganizationAdminOut(BaseModel):
    id: str
    name: str
    slug: str
    type: str
    subdomain: str | None
    is_active: bool
    surfaces: list[str]


class UserAdminIn(BaseModel):
    organization_id: str
    email: str
    name: str
    role: str
    password: str | None = None
    username: str | None = None
    is_active: bool = True
    perm_patient_engagement: bool = False
    perm_community_engagement: bool = False
    perm_revisit_engagement: bool = False
    perm_escalation_engagement: bool = False
    perm_calllog_engagement: bool = False
    perm_upload_engagement: bool = False
    perm_pdf_engagement: bool = False

    @field_validator("email", "username", mode="before")
    @classmethod
    def normalize_identifier(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else None


class UserAdminOut(BaseModel):
    id: str
    organization_id: str
    email: str
    username: str | None
    name: str
    role: str
    is_active: bool
    permissions: dict[str, bool]
