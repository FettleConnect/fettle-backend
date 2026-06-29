from __future__ import annotations

from pydantic import BaseModel


class MigrationStatus(BaseModel):
    product: str
    phase: str
    implemented: list[str]
    pending: list[str]
