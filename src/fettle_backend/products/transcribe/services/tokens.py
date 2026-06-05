from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_legacy_doctor_token(doctor_id: str, token_version: int, secret: str) -> str:
    payload = {
        "doctorId": doctor_id,
        "tokenVersion": token_version,
        "issuedAt": datetime.now(UTC).isoformat(),
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = _b64_encode(payload_raw)
    signature = hmac.new(secret.encode("utf-8"), payload_part.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload_part}.{_b64_encode(signature)}"


def verify_legacy_doctor_token(token: str, secret: str) -> dict[str, object] | None:
    try:
        payload_part, signature_part = token.split(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(secret.encode("utf-8"), payload_part.encode("utf-8"), hashlib.sha256).digest()
    actual_signature = _b64_decode(signature_part)
    if not hmac.compare_digest(expected_signature, actual_signature):
        return None

    try:
        return json.loads(_b64_decode(payload_part).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
