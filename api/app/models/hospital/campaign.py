from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TenantMixin


class Campaign(TenantMixin, Base):
    """Outbound calling campaign targeting a set of patients."""

    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # health_package | new_facility | discounted_product | custom
    template_type: Mapped[str | None] = mapped_column(String(50))
    audience_type: Mapped[str | None] = mapped_column(String(50))
    # [uuid, uuid, ...] — patient IDs targeted by this campaign
    patient_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # Template-specific fields
    package_name: Mapped[str | None] = mapped_column(String(255))
    facility_name: Mapped[str | None] = mapped_column(String(255))
    discount_details: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)

    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    unconnected_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # created | running | paused | completed | failed
    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
