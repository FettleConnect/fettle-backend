from __future__ import annotations

from pydantic import BaseModel


class BrandingResponse(BaseModel):
    hospital_name: str
    branding: dict
    agent_prompt_prefix: str | None = None
    vobiz_did: str | None = None
    whatsapp_channel_id: str | None = None
