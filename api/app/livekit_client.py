from __future__ import annotations

import os

import httpx

LIVEKIT_AGENT_URL = os.getenv("LIVEKIT_AGENT_URL", "http://livekit-agent:8080")


async def dispatch_call(phone: str, id_key: str, metadata: dict | None = None) -> dict:
    payload = {
        "customer": {"number": phone, "id_key": id_key},
        "metadata": metadata or {},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{LIVEKIT_AGENT_URL.rstrip('/')}/dispatch", json=payload
        )
        try:
            return resp.json()
        except ValueError:
            return {"error": 1, "errorMsg": "invalid response from agent"}


def dispatch_call_sync(phone: str, id_key: str, metadata: dict | None = None) -> dict:
    payload = {
        "customer": {"number": phone, "id_key": id_key},
        "metadata": metadata or {},
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{LIVEKIT_AGENT_URL.rstrip('/')}/dispatch", json=payload)
        try:
            return resp.json()
        except ValueError:
            return {"error": 1, "errorMsg": "invalid response from agent"}
