from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import HTTPException

from api.app.core.config import Settings
from api.app.schemas.hospital.transcribe import ConsultationSession, ErrorResponse


class FettleSyncService:
    def __init__(self, repository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.fettle_sync_enabled

    async def enqueue_session_sync(
        self, session: ConsultationSession, trigger: str
    ) -> dict | None:
        if not self.enabled:
            return None
        now = datetime.now(UTC)
        payload = build_fettle_payload(session, trigger)
        sync_key = build_sync_key(session.id, trigger, payload)
        payload["medivoiceSyncKey"] = sync_key
        return await self.repository.enqueue(
            {
                "sessionId": session.id,
                "trigger": trigger,
                "idempotencyKey": sync_key,
                "payload": payload,
                "maxAttempts": self.settings.fettle_sync_max_attempts,
                "queuedAt": now,
            }
        )

    async def enqueue_dry_run(self) -> dict:
        self._ensure_enabled()
        now = datetime.now(UTC)
        sync_key = f"dry-run:{now.isoformat()}:{uuid.uuid4()}"
        return await self.repository.enqueue(
            {
                "sessionId": "dry-run",
                "trigger": "dry_run",
                "idempotencyKey": sync_key,
                "payload": {
                    "medivoiceSessionId": "dry-run",
                    "medivoiceSyncKey": sync_key,
                    "trigger": "dry_run",
                    "doctorEmail": "dry-run@fettleconnect.com",
                    "patientName": "KPI Dry Run",
                    "patientMobile": "0000000000",
                    "patientEmail": "",
                    "overallSummary": "Dry run payload for deployment verification.",
                    "transcriptions": [],
                    "metaData": {},
                },
                "maxAttempts": 1,
                "queuedAt": now,
            }
        )

    async def process_due_jobs(self, limit: int = 10) -> dict:
        self._ensure_enabled()
        now = datetime.now(UTC)
        jobs = await self.repository.claim_due_jobs(limit, now)
        succeeded = 0
        failed = 0
        dead_letter = 0
        for job in jobs:
            if job["trigger"] == "dry_run":
                await self.repository.mark_succeeded(job["id"], datetime.now(UTC))
                succeeded += 1
                continue
            try:
                await self._send_job(job)
                await self.repository.mark_succeeded(job["id"], datetime.now(UTC))
                succeeded += 1
            except Exception as exc:
                should_dead_letter = int(job["attempts"]) >= int(job["maxAttempts"])
                status = "dead_letter" if should_dead_letter else "queued"
                if should_dead_letter:
                    dead_letter += 1
                else:
                    failed += 1
                await self.repository.mark_failed(
                    job["id"],
                    status=status,
                    next_attempt_at=datetime.now(UTC)
                    + timedelta(seconds=self.settings.fettle_sync_retry_delay_seconds),
                    last_error=str(exc),
                    last_error_class=type(exc).__name__,
                    finished_at=datetime.now(UTC) if should_dead_letter else None,
                )
        return {
            "processed": len(jobs),
            "succeeded": succeeded,
            "failed": failed,
            "deadLetter": dead_letter,
        }

    async def get_stats(self) -> dict:
        return await self.repository.get_stats(self.enabled)

    async def list_jobs(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict]:
        return await self.repository.list_jobs(status=status, limit=limit)

    async def replay_dead_letter(self, job_id: str) -> dict | None:
        self._ensure_enabled()
        return await self.repository.replay_dead_letter(job_id, datetime.now(UTC))

    async def _send_job(self, job: dict) -> None:
        if not self.settings.fettle_backend_url or not self.settings.fettle_sync_secret:
            raise RuntimeError(
                "Fettle sync is enabled but backend URL or secret is missing."
            )
        async with httpx.AsyncClient(
            timeout=self.settings.fettle_sync_timeout_seconds
        ) as client:
            response = await client.post(
                f"{self.settings.fettle_backend_url.rstrip('/')}/api/medivoice/sync/",
                headers={
                    "Content-Type": "application/json",
                    "X-Fettle-Secret": self.settings.fettle_sync_secret,
                    "X-Idempotency-Key": job["idempotencyKey"],
                },
                content=json.dumps(job["payload"]),
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Fettle sync failed with status {response.status_code}: {response.text[:500]}"
            )

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    message="Fettle sync is not configured."
                ).model_dump(),
            )


def build_fettle_payload(session: ConsultationSession, trigger: str) -> dict:
    transcription = session.transcription
    segments = transcription.transcriptSegments if transcription else []
    return {
        "medivoiceSessionId": session.id,
        "medivoiceSyncKey": "",
        "trigger": trigger,
        "doctorEmail": "",
        "patientName": session.patient.name,
        "patientMobile": session.patient.phoneNumber,
        "patientEmail": "",
        "overallSummary": (
            transcription.summary if transcription and transcription.summary else ""
        ),
        "transcriptions": [
            {
                "speaker": segment.speakerLabel,
                "text": segment.text,
                "timestamp": segment.startedAtSeconds,
            }
            for segment in segments
            if segment.text.strip()
        ],
        "metaData": {
            "doctorId": session.doctorId,
            "caseStatus": session.caseStatus,
            "status": session.status,
        },
    }


def build_sync_key(session_id: str, trigger: str, payload: dict) -> str:
    payload_digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"{session_id}:{trigger}:{payload_digest}"
