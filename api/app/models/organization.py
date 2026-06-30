from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.app.db.base import Base
from api.app.models.base import TimestampMixin


class Organization(TimestampMixin, Base):
    """Unified tenant record — covers hospitals, corporate clients, and clinical tenants."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    subdomain: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    # Discriminator: "hospital" | "corporate" | "clinical"
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    hospital_config: Mapped[HospitalConfig | None] = relationship(
        "HospitalConfig",
        back_populates="organization",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    corporate_config: Mapped[CorporateConfig | None] = relationship(
        "CorporateConfig",
        back_populates="organization",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HospitalConfig(TimestampMixin, Base):
    """Hospital-specific operational config — one row per hospital organization."""

    __tablename__ = "hospital_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    reception_email: Mapped[str | None] = mapped_column(String(255))
    pharmacy_email: Mapped[str | None] = mapped_column(String(255))

    # Vobiz SIP — outbound caller ID + trunk routing
    vobiz_did: Mapped[str | None] = mapped_column(String(20), index=True)
    vobiz_sip_trunk_id: Mapped[str | None] = mapped_column(String(100))

    # Vobiz WhatsApp
    whatsapp_channel_id: Mapped[str | None] = mapped_column(String(100))
    whatsapp_waba_id: Mapped[str | None] = mapped_column(String(100))

    # AI agent per-tenant customisation
    agent_prompt_prefix: Mapped[str | None] = mapped_column(Text)
    # {"logo_url": "https://...", "primary_color": "#RRGGBB"}
    branding_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Legacy VAPI assistant ID (superseded by LiveKit dispatch)
    vapi_assistant_id: Mapped[str | None] = mapped_column(String(100))

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="hospital_config"
    )


class CorporateConfig(TimestampMixin, Base):
    """Corporate-specific operational config — one row per corporate organization."""

    __tablename__ = "corporate_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, insert_default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    company_name: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(100))
    s3_bucket: Mapped[str | None] = mapped_column(String(255))
    # {"logo_url": "https://...", "primary_color": "#RRGGBB"}
    branding_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="corporate_config"
    )
