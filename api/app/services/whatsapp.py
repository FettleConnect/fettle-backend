from __future__ import annotations

import re

import httpx

from api.app.core.config import settings

_VOBIZ_MSG_URL = f"{settings.vobiz_api_url}/api/v1/messaging/messages"


def _e164(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if not digits.startswith("91") and len(digits) == 10:
        digits = "91" + digits
    return "+" + digits if not digits.startswith("+") else digits


async def send_whatsapp(
    *,
    to_number: str,
    msg: str | None = None,
    media_url: str | None = None,
    media_type: str = "image",
    template_name: str | None = None,
    template_lang: str = "en_US",
    template_params: list[str] | None = None,
    channel_id: str | None = None,
    waba_id: str | None = None,
) -> dict:
    """Send a WhatsApp message via Vobiz API. Returns response JSON."""
    to = _e164(to_number)
    headers = {
        "X-Auth-ID": settings.vobiz_api_key,
        "X-Auth-Token": settings.vobiz_api_key,
        "Content-Type": "application/json",
    }

    if template_name:
        components = []
        if template_params:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": p} for p in template_params
                    ],
                }
            )
        payload: dict = {
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": template_lang},
                "components": components,
            },
        }
    elif media_url:
        media_body: dict = {"link": media_url}
        payload = {"to": to, "type": media_type, media_type: media_body}
    else:
        payload = {"to": to, "type": "text", "text": {"body": msg or ""}}

    if channel_id:
        payload["channel_id"] = channel_id
    if waba_id:
        payload["waba_id"] = waba_id

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_VOBIZ_MSG_URL, json=payload, headers=headers)
            return resp.json()
    except Exception as exc:
        return {"error": 1, "errorMsg": str(exc)}
