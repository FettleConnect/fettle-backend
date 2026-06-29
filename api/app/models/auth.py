from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TimestampMixin


class User(TimestampMixin, Base):
    """
    Unified user — replaces hospital_user, admin, doctor (hospital domain)
    and User (corporate domain).

    Role values:
      superadmin    — cross-org platform admin
      hospital_admin — hospital org admin
      hospital_user  — hospital staff with granular permissions
      doctor         — has Doctor-specific fields populated
      corporate_admin
      hr             — corporate HR manager
      employee       — corporate employee
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Hospital user permission flags ───────────────────────────────────────
    perm_patient_engagement: Mapped[bool] = mapped_column(Boolean, default=False)
    perm_community_engagement: Mapped[bool] = mapped_column(Boolean, default=False)
    perm_revisit_engagement: Mapped[bool] = mapped_column(Boolean, default=False)
    perm_escalation_engagement: Mapped[bool] = mapped_column(Boolean, default=False)
    perm_calllog_engagement: Mapped[bool] = mapped_column(Boolean, default=False)
    perm_upload_engagement: Mapped[bool] = mapped_column(Boolean, default=False)
    perm_pdf_engagement: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Doctor-specific fields (populated when role == "doctor") ─────────────
    mobile_number: Mapped[str | None] = mapped_column(String(20))
    department: Mapped[str | None] = mapped_column(String(100))
    # [{day: "Monday", slots: ["09:00","10:00"]}]
    availability_json: Mapped[dict | None] = mapped_column(JSONB)
    timings: Mapped[str | None] = mapped_column(String(255))
    consultation_fee: Mapped[float | None] = mapped_column(Numeric(10, 2))
    legacy_pin_code: Mapped[str | None] = mapped_column(String(16))
    legacy_token_version: Mapped[int] = mapped_column(default=0, nullable=False)
    must_change_pin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # ── Corporate employee fields ────────────────────────────────────────────
    company: Mapped[str | None] = mapped_column(String(255))
    corp_department: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    phone_number: Mapped[str | None] = mapped_column(String(20))
    profile_picture_url: Mapped[str | None] = mapped_column(Text)
    saved_addresses_json: Mapped[list | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )
