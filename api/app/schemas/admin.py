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
    type: str = Field(pattern="^(hospital|corporate)$")
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


class AdminMetric(BaseModel):
    label: str
    value: int


class AdminDashboardMetrics(BaseModel):
    organizations: int
    hospitals: int
    corporate_clients: int
    users: int
    doctors: int
    patients: int
    inbound_calls: int
    outbound_calls: int


class HospitalAdminSummary(BaseModel):
    id: str
    name: str
    slug: str
    subdomain: str | None
    is_active: bool
    patient_count: int
    doctor_count: int
    user_count: int
    inbound_call_count: int
    outbound_call_count: int


class HospitalUserAdminIn(BaseModel):
    email: str | None = None
    name: str
    role: str = Field(
        default="hospital_user", pattern="^(hospital_admin|hospital_user|doctor)$"
    )
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


class UserPasswordAdminIn(BaseModel):
    password: str


class UserAdminIn(BaseModel):
    organization_id: str
    email: str | None = None
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
    email: str | None
    username: str | None
    name: str
    role: str
    is_active: bool
    permissions: dict[str, bool]


class HospitalAdminDetail(BaseModel):
    hospital: HospitalAdminSummary
    users: list[UserAdminOut]


class AdminDbColumn(BaseModel):
    name: str
    type: str
    primary_key: bool
    nullable: bool
    default: str | None = None


class AdminDbTable(BaseModel):
    name: str
    columns: list[AdminDbColumn]
    primary_key: list[str]


class AdminDbRows(BaseModel):
    table: AdminDbTable
    rows: list[dict[str, object | None]]
    limit: int
    offset: int


class AdminDbMutation(BaseModel):
    values: dict[str, object | None]


class AdminDbUpdate(BaseModel):
    pk: dict[str, object | None]
    values: dict[str, object | None]


class AdminDbDelete(BaseModel):
    pk: dict[str, object | None]
