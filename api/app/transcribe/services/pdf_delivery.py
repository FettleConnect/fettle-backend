from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import HTTPException

from api.app.core.config import Settings
from api.app.transcribe.storage import LocalStorage
from api.app.transcribe.schemas.common import ErrorResponse


class PdfDeliveryService:
    def __init__(self, repository, settings: Settings, storage: LocalStorage | None = None) -> None:
        self.repository = repository
        self.settings = settings
        self.storage = storage or LocalStorage()

    async def prepare_session_delivery(
        self,
        session_id: str,
        body: dict,
        doctor_id: str | None = None,
    ) -> dict:
        await self._load_session(session_id, doctor_id)
        parsed = self._parse_settings_body(body)
        saved = await self.repository.save_pdf_delivery_settings(session_id, **parsed)
        if saved is None:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Session not found.").model_dump())
        return await self._status_or_404(session_id)

    async def get_session_delivery_status(self, session_id: str, doctor_id: str | None = None) -> dict:
        await self._load_session(session_id, doctor_id)
        return await self._status_or_404(session_id)

    async def send_session_delivery(
        self,
        session_id: str,
        body: dict,
        doctor_id: str | None = None,
    ) -> dict:
        session = await self._load_session(session_id, doctor_id)
        if session.documentArtifact is None:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(message="Generate the consultation PDF before sending it on WhatsApp.").model_dump(),
            )
        parsed = self._parse_settings_body(body)
        await self.repository.save_pdf_delivery_settings(session_id, **parsed)
        await self.repository.enqueue_pdf_delivery_jobs(session_id)
        return await self._status_or_404(session_id)

    async def enqueue_for_session(self, session_id: str) -> list[dict]:
        jobs = await self.repository.enqueue_pdf_delivery_jobs(session_id)
        return jobs or []

    async def process_pending_jobs(self, limit: int = 10) -> list[dict]:
        claimed = await self.repository.claim_pending_pdf_delivery_jobs(limit)
        processed: list[dict] = []
        for job in claimed:
            try:
                provider_message_id = f"mock-{job['id']}"
                updated = await self.repository.update_pdf_delivery_job_status(
                    job["id"],
                    "queued",
                    provider="mock-whatsapp",
                    provider_message_id=provider_message_id,
                    sent_at=datetime.now(UTC),
                )
                if updated is not None:
                    processed.append(updated)
            except Exception as exc:
                failed = await self.repository.update_pdf_delivery_job_status(
                    job["id"],
                    "failed",
                    last_error=str(exc),
                )
                if failed is not None:
                    processed.append(failed)
        return processed

    async def list_jobs_for_admin(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        recipient_type: str | None = None,
    ) -> list[dict]:
        return await self.repository.list_pdf_delivery_jobs_admin(
            session_id=session_id,
            status=status,
            recipient_type=recipient_type,
        )

    async def get_job_by_id_for_admin(self, job_id: str) -> dict | None:
        return await self.repository.get_pdf_delivery_job(job_id)

    async def handle_provider_status_update(
        self,
        *,
        provider_message_id: str,
        provider_status: str | None,
        error_message: str | None = None,
    ) -> dict | None:
        job = await self.repository.get_pdf_delivery_job_by_provider_message_id(provider_message_id)
        if job is None:
            return None
        next_status = map_twilio_message_status(provider_status)
        if not should_apply_provider_status_transition(job["status"], next_status):
            return job
        return await self.repository.update_pdf_delivery_job_status(
            job["id"],
            next_status,
            last_error=error_message if next_status == "failed" else None,
            sent_at=datetime.now(UTC) if next_status != "failed" else None,
        )

    async def get_public_media_asset(self, token: str) -> tuple[str, str, bytes] | None:
        session_id = self._resolve_session_id_from_media_token(token)
        if session_id is None:
            return None
        file_model = await self.repository.get_pdf_download_any(session_id)
        if file_model is None:
            return None
        return (
            file_model.original_filename,
            file_model.content_type,
            self.storage.read_bytes(file_model.storage_key),
        )

    def create_public_media_url(self, session_id: str, file_name: str) -> str:
        base_url = self.settings.pdf_delivery_public_base_url.rstrip("/")
        public_file_name = sanitize_public_media_file_name(file_name)
        token = self._create_media_token(session_id)
        return f"{base_url}/public/pdf-delivery/media/{quote(public_file_name)}?token={quote(token)}"

    async def _load_session(self, session_id: str, doctor_id: str | None):
        if doctor_id is None:
            session = await self.repository.get_session_any(session_id)
        else:
            session = await self.repository.get_session(session_id, doctor_id)
        if session is None:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Session not found.").model_dump())
        return session

    async def _status_or_404(self, session_id: str) -> dict:
        status = await self.repository.get_pdf_delivery_status(session_id)
        if status is None:
            raise HTTPException(status_code=404, detail=ErrorResponse(message="Session not found.").model_dump())
        return status

    def _parse_settings_body(self, body: dict) -> dict:
        patient_enabled = bool(body.get("patientEnabled", True))
        pharmacy_enabled = bool(body.get("pharmacyEnabled", False))
        pharmacy = body.get("pharmacy") if isinstance(body.get("pharmacy"), dict) else None
        if pharmacy_enabled:
            pharmacy_name = str((pharmacy or {}).get("name", "")).strip()
            pharmacy_phone = str((pharmacy or {}).get("phoneNumber", "")).strip()
            if not pharmacy_name or not pharmacy_phone:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorResponse(
                        message="Pharmacy name and phone number are required when pharmacy delivery is enabled."
                    ).model_dump(),
                )
            pharmacy = {"name": pharmacy_name, "phoneNumber": pharmacy_phone}
        else:
            pharmacy = None
        return {
            "patient_enabled": patient_enabled,
            "pharmacy_enabled": pharmacy_enabled,
            "pharmacy": pharmacy,
        }

    def _create_media_token(self, session_id: str) -> str:
        payload = {
            "sessionId": session_id,
            "exp": int((datetime.now(UTC) + timedelta(seconds=self.settings.pdf_delivery_token_ttl_seconds)).timestamp()),
        }
        encoded_payload = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        signature = hmac.new(self._token_secret(), encoded_payload.encode(), hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        return f"{encoded_payload}.{encoded_signature}"

    def _resolve_session_id_from_media_token(self, token: str) -> str | None:
        try:
            encoded_payload, received_signature = token.split(".", 1)
        except ValueError:
            return None
        expected_signature = (
            base64.urlsafe_b64encode(hmac.new(self._token_secret(), encoded_payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        )
        if not hmac.compare_digest(expected_signature, received_signature):
            return None
        try:
            payload_bytes = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
            payload = json.loads(payload_bytes.decode())
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("sessionId")
        exp = payload.get("exp")
        if not isinstance(session_id, str) or not isinstance(exp, int):
            return None
        if exp <= int(datetime.now(UTC).timestamp()):
            return None
        return session_id

    def _token_secret(self) -> bytes:
        return (self.settings.pdf_delivery_token_secret or self.settings.legacy_auth_secret).encode()


def map_twilio_message_status(status: str | None) -> str:
    match status:
        case "queued" | "accepted" | "scheduled" | "sending":
            return "queued"
        case "sent":
            return "sent"
        case "delivered":
            return "delivered"
        case "read":
            return "read"
        case _:
            return "failed"


def should_apply_provider_status_transition(current_status: str, next_status: str) -> bool:
    if current_status == "failed" and next_status != "failed":
        return False
    if current_status == "read":
        return False
    order = {"pending": 0, "processing": 1, "queued": 2, "sent": 3, "delivered": 4, "read": 5, "failed": 6}
    return order.get(next_status, 0) >= order.get(current_status, 0) or next_status == "failed"


def sanitize_public_media_file_name(file_name: str) -> str:
    trimmed = file_name.strip() or "consultation-summary.pdf"
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", trimmed)
    return normalized if normalized.lower().endswith(".pdf") else f"{normalized}.pdf"
