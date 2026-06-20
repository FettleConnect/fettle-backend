from __future__ import annotations


from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db.base import Base
from api.app.models.base import TimestampMixin


class GlobalConfig(TimestampMixin, Base):
    """
    Key-value configuration store — platform-wide settings not tied to a tenant.
    Replaces Django's GlobalConfig chatbot model.
    """

    __tablename__ = "global_configs"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
