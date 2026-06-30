from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from api.app.core.config import Settings
from api.app.transcribe.schemas.common import ConsultationSession

OPENAI_BASE_URL = "https://api.openai.com/v1"
SONIOX_BASE_URL = "https://api.soniox.com"
SONIOX_POLL_INTERVAL_SECONDS = 2.0
SONIOX_POLL_TIMEOUT_SECONDS = 180.0

OPENAI_SYNTHESIS_PROMPT = """
You are generating a final structured medical consultation output from an existing transcript.
Write summary, metadata, and draftDocument in clear clinical English.
Return JSON only with this exact top-level shape:
{
  "summary": string,
  "metadata": {
    "chiefComplaint"?: string,
    "symptoms": string[],
    "diagnoses": string[],
    "followUpPlan"?: string,
    "careTeamNotes": string[]
  },
  "draftDocument": {
    "title": string,
    "chiefComplaint"?: string,
    "illnessHistory"?: string,
    "clinicalSummary": string,
    "medications": [
      {
        "medicationName": string,
        "dosage": string,
        "instructions": string
      }
    ],
    "testsPrescribed": string[],
    "revisitDate"?: string,
    "nextSteps": string[],
    "cautions": string[]
  }
}
Do not wrap JSON in markdown fences.
Use empty arrays instead of null.
Do not invent facts not present in the transcript.
If any investigations, labs, scans, or imaging are advised, put them in draftDocument.testsPrescribed.
Put a value in draftDocument.revisitDate only when the conversation explicitly mentions a follow-up date or timing.
draftDocument.revisitDate must be strict ISO format YYYY-MM-DD or omitted.
""".strip()


class TranscriptionProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class StoredAudioInput:
    file_name: str
    mime_type: str
    data: bytes
    duration_seconds: float


class BaseTranscriptionProvider:
    provider_name = "mock-gemini"

    async def transcribe(
        self,
        session: ConsultationSession,
        audio: StoredAudioInput,
    ) -> dict[str, Any]:
        raise NotImplementedError


class LocalTranscriptionProvider(BaseTranscriptionProvider):
    provider_name = "mock-gemini"

    async def transcribe(
        self,
        session: ConsultationSession,
        audio: StoredAudioInput,
    ) -> dict[str, Any]:
        duration = max(30, round(audio.duration_seconds if audio.duration_seconds else 90))
        midpoint = max(12, round(duration / 2))
        doctor_label = session.doctor.name or "Doctor"
        patient_label = session.patient.name or "Patient"
        segments = [
            {
                "id": str(uuid.uuid4()),
                "speaker": "doctor",
                "speakerLabel": doctor_label,
                "text": f"Good morning {patient_label}. Tell me what has been bothering you today.",
                "startedAtSeconds": 0,
                "endedAtSeconds": max(8, midpoint - 10),
            },
            {
                "id": str(uuid.uuid4()),
                "speaker": "patient",
                "speakerLabel": patient_label,
                "text": "I have had a persistent cough, throat irritation, and mild fever for the last three days.",
                "startedAtSeconds": max(8, midpoint - 9),
                "endedAtSeconds": midpoint,
            },
            {
                "id": str(uuid.uuid4()),
                "speaker": "doctor",
                "speakerLabel": doctor_label,
                "text": (
                    "This sounds like an upper respiratory infection. Stay hydrated, take the prescribed medicines, "
                    "and come back if breathing worsens."
                ),
                "startedAtSeconds": midpoint,
                "endedAtSeconds": duration,
            },
        ]
        summary = (
            f"{patient_label} reported persistent cough, throat irritation, and mild fever for three days. "
            "Supportive care, medication, and review precautions were advised."
        )
        return {
            "provider": self.provider_name,
            "requiresRecovery": False,
            "summary": summary,
            "transcriptSegments": segments,
            "metadata": {
                "chiefComplaint": "Persistent cough and mild fever",
                "symptoms": ["cough", "throat irritation", "mild fever"],
                "diagnoses": ["Upper respiratory infection"],
                "followUpPlan": "Review again if symptoms worsen or breathing difficulty develops.",
                "careTeamNotes": ["Hydration encouraged", "Monitor fever trend"],
            },
            "draftDocument": {
                "title": "Consultation Draft",
                "clinicalSummary": "Symptoms are consistent with an uncomplicated upper respiratory infection.",
                "medications": [
                    {
                        "medicationName": "Paracetamol",
                        "dosage": "500 mg",
                        "instructions": "Take one tablet every 6 to 8 hours if fever persists.",
                    },
                    {
                        "medicationName": "Cough Syrup",
                        "dosage": "10 ml",
                        "instructions": "Take twice daily after food for five days.",
                    },
                ],
                "testsPrescribed": ["CBC"],
                "nextSteps": [
                    "Continue hydration and rest.",
                    "Return for review if symptoms worsen.",
                ],
                "cautions": ["Seek urgent help if shortness of breath develops."],
            },
        }


class OpenAITranscriptSynthesizer:
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.openai_api_key
        self.base_url = (settings.openai_base_url or OPENAI_BASE_URL).rstrip("/")
        self.model = settings.openai_synthesis_model
        self.timeout_seconds = settings.transcription_timeout_seconds

    async def synthesize(
        self,
        session: ConsultationSession,
        transcript_segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.api_key:
            raise TranscriptionProviderError(
                "OPENAI_API_KEY is required for transcript synthesis."
            )
        transcript_text = "\n".join(
            (
                f"[{round(float(segment.get('startedAtSeconds', 0)))}-"
                f"{round(float(segment.get('endedAtSeconds', segment.get('startedAtSeconds', 0))))}s] "
                f"{segment.get('speakerLabel', 'Speaker')}: {segment.get('text', '')}"
            )
            for segment in transcript_segments
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "\n\n".join(
                                        [
                                            OPENAI_SYNTHESIS_PROMPT,
                                            f"Today: {date.today().isoformat()}",
                                            f"Patient: {session.patient.name}",
                                            f"Doctor: {session.doctor.name}",
                                            "Transcript:",
                                            transcript_text,
                                        ]
                                    ),
                                }
                            ],
                        }
                    ],
                    "text": {"format": {"type": "json_object"}},
                },
            )
        if response.status_code >= 400:
            raise TranscriptionProviderError(
                f"OpenAI transcript synthesis failed with status {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        text = _extract_openai_response_text(payload)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TranscriptionProviderError(
                "OpenAI transcript synthesis returned malformed JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise TranscriptionProviderError(
                "OpenAI transcript synthesis returned an unexpected payload."
            )
        return {
            "summary": str(parsed.get("summary", "")).strip(),
            "metadata": parsed.get("metadata", {}) if isinstance(parsed.get("metadata"), dict) else {},
            "draftDocument": (
                parsed.get("draftDocument", {})
                if isinstance(parsed.get("draftDocument"), dict)
                else {}
            ),
        }


class OpenAITranscriptionProvider(BaseTranscriptionProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise TranscriptionProviderError(
                "OPENAI_API_KEY is required when AI_PROVIDER=openai."
            )
        self.api_key = settings.openai_api_key
        self.base_url = (settings.openai_base_url or OPENAI_BASE_URL).rstrip("/")
        self.transcribe_model = settings.openai_transcribe_model
        self.timeout_seconds = settings.transcription_timeout_seconds
        self.provider_name = (
            f"openai:{self.transcribe_model}+{settings.openai_synthesis_model}"
        )
        self.synthesizer = OpenAITranscriptSynthesizer(settings)

    async def transcribe(
        self,
        session: ConsultationSession,
        audio: StoredAudioInput,
    ) -> dict[str, Any]:
        transcript_segments = await self._request_audio_transcription(session, audio)
        synthesis = await self.synthesizer.synthesize(session, transcript_segments)
        return {
            "provider": self.provider_name,
            "requiresRecovery": False,
            "summary": synthesis["summary"],
            "transcriptSegments": transcript_segments,
            "metadata": synthesis["metadata"],
            "draftDocument": synthesis["draftDocument"],
        }

    async def _request_audio_transcription(
        self, session: ConsultationSession, audio: StoredAudioInput
    ) -> list[dict[str, Any]]:
        files = {
            "file": (
                audio.file_name,
                audio.data,
                audio.mime_type or "application/octet-stream",
            )
        }
        data = {
            "model": self.transcribe_model,
            "response_format": "json",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files=files,
            )
        if response.status_code >= 400:
            raise TranscriptionProviderError(
                f"OpenAI transcription failed with status {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        return _extract_openai_segments(payload, session)


class SonioxTranscriptionProvider(BaseTranscriptionProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.soniox_api_key:
            raise TranscriptionProviderError(
                "SONIOX_API_KEY is required when AI_PROVIDER=soniox."
            )
        if not settings.openai_api_key:
            raise TranscriptionProviderError(
                "OPENAI_API_KEY is required for Soniox transcript synthesis."
            )
        self.api_key = settings.soniox_api_key
        self.base_url = (settings.soniox_base_url or SONIOX_BASE_URL).rstrip("/")
        self.model = settings.soniox_model
        self.timeout_seconds = settings.transcription_timeout_seconds
        self.poll_interval_seconds = settings.soniox_poll_interval_seconds
        self.poll_timeout_seconds = settings.soniox_poll_timeout_seconds
        self.language_hints = settings.soniox_language_hints
        self.provider_name = f"soniox:{self.model}+openai:{settings.openai_synthesis_model}"
        self.synthesizer = OpenAITranscriptSynthesizer(settings)

    async def transcribe(
        self,
        session: ConsultationSession,
        audio: StoredAudioInput,
    ) -> dict[str, Any]:
        file_id = await self._upload_file(audio)
        try:
            transcription_id = await self._create_transcription(session, file_id)
            tokens = await self._wait_for_transcript(transcription_id)
        finally:
            await self._delete_file(file_id)
        transcript_segments = _build_soniox_segments(tokens, session)
        synthesis = await self.synthesizer.synthesize(session, transcript_segments)
        return {
            "provider": self.provider_name,
            "requiresRecovery": False,
            "summary": synthesis["summary"],
            "transcriptSegments": transcript_segments,
            "metadata": synthesis["metadata"],
            "draftDocument": synthesis["draftDocument"],
        }

    async def _upload_file(self, audio: StoredAudioInput) -> str:
        files = {
            "file": (
                audio.file_name,
                audio.data,
                audio.mime_type or "application/octet-stream",
            )
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files,
            )
        if response.status_code >= 400:
            raise TranscriptionProviderError(
                f"Soniox file upload failed with status {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        file_id = payload.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise TranscriptionProviderError("Soniox file upload returned no file id.")
        return file_id

    async def _create_transcription(
        self, session: ConsultationSession, file_id: str
    ) -> str:
        body: dict[str, Any] = {
            "file_id": file_id,
            "model": self.model,
            "context": {
                "general": [
                    {"key": "domain", "value": "Healthcare"},
                    {"key": "topic", "value": "Doctor consultation"},
                    {"key": "doctor", "value": session.doctor.name},
                    {"key": "patient", "value": session.patient.name},
                ],
                "text": (
                    "Two-speaker clinical consultation. The doctor asks questions, gives advice, and confirms follow-up plans. "
                    "The patient describes symptoms, answers questions, and confirms next steps. Preserve who said what."
                ),
            },
            "enable_speaker_diarization": True,
            "enable_language_identification": True,
        }
        if self.language_hints:
            body["language_hints"] = self.language_hints
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/transcriptions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if response.status_code >= 400:
            raise TranscriptionProviderError(
                f"Soniox transcription create failed with status {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        transcription_id = payload.get("id")
        if not isinstance(transcription_id, str) or not transcription_id:
            raise TranscriptionProviderError(
                "Soniox transcription creation returned no transcription id."
            )
        return transcription_id

    async def _wait_for_transcript(
        self, transcription_id: str
    ) -> list[dict[str, Any]]:
        elapsed = 0.0
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            while elapsed < self.poll_timeout_seconds:
                status_response = await client.get(
                    f"{self.base_url}/v1/transcriptions/{transcription_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if status_response.status_code >= 400:
                    raise TranscriptionProviderError(
                        f"Soniox transcription status failed with status {status_response.status_code}: {status_response.text[:500]}"
                    )
                status_payload = status_response.json()
                status = status_payload.get("status")
                if status == "completed":
                    break
                if status == "error":
                    raise TranscriptionProviderError(
                        f"Soniox transcription failed: {status_payload.get('error_message', 'unknown processing error')}."
                    )
                await asyncio.sleep(self.poll_interval_seconds)
                elapsed += self.poll_interval_seconds
            else:
                raise TranscriptionProviderError(
                    "Soniox transcription did not complete before the polling timeout."
                )
            transcript_response = await client.get(
                f"{self.base_url}/v1/transcriptions/{transcription_id}/transcript",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        if transcript_response.status_code >= 400:
            raise TranscriptionProviderError(
                f"Soniox transcript fetch failed with status {transcript_response.status_code}: {transcript_response.text[:500]}"
            )
        payload = transcript_response.json()
        tokens = payload.get("tokens", [])
        return tokens if isinstance(tokens, list) else []

    async def _delete_file(self, file_id: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await client.delete(
                f"{self.base_url}/v1/files/{file_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )


def create_transcription_provider(settings: Settings) -> BaseTranscriptionProvider:
    provider = settings.ai_provider
    if provider == "auto":
        if settings.soniox_api_key:
            return SonioxTranscriptionProvider(settings)
        if settings.openai_api_key:
            return OpenAITranscriptionProvider(settings)
        return LocalTranscriptionProvider()
    if provider == "openai":
        return OpenAITranscriptionProvider(settings)
    if provider == "soniox":
        return SonioxTranscriptionProvider(settings)
    if provider == "mock":
        return LocalTranscriptionProvider()
    if settings.soniox_api_key:
        return SonioxTranscriptionProvider(settings)
    if settings.openai_api_key:
        return OpenAITranscriptionProvider(settings)
    return LocalTranscriptionProvider()


def _extract_openai_segments(
    payload: dict[str, Any], session: ConsultationSession
) -> list[dict[str, Any]]:
    segments = payload.get("segments")
    if isinstance(segments, list) and segments:
        normalized: list[dict[str, Any]] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            normalized.append(
                {
                    "id": str(segment.get("id") or uuid.uuid4()),
                    "speaker": _normalize_openai_speaker(segment.get("speaker")),
                    "speakerLabel": _speaker_label_for_segment(
                        _normalize_openai_speaker(segment.get("speaker")),
                        session,
                    ),
                    "text": str(segment.get("text", "")).strip(),
                    "startedAtSeconds": round(float(segment.get("start", 0) or 0)),
                    "endedAtSeconds": round(
                        float(segment.get("end", segment.get("start", 0)) or 0)
                    ),
                }
            )
        normalized = [segment for segment in normalized if segment["text"]]
        if normalized:
            return normalized
    text = str(payload.get("text", "")).strip()
    if not text:
        raise TranscriptionProviderError(
            "OpenAI transcription returned no transcript text."
        )
    return [
        {
            "id": str(uuid.uuid4()),
            "speaker": "unknown",
            "speakerLabel": "Consultation",
            "text": text,
            "startedAtSeconds": 0,
            "endedAtSeconds": round(
                session.audioArtifact.durationSeconds if session.audioArtifact else 0
            ),
        }
    ]


def _extract_openai_response_text(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    return part["text"]
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    raise TranscriptionProviderError(
        "OpenAI transcript synthesis returned no text."
    )


def _normalize_openai_speaker(value: Any) -> str:
    speaker = str(value or "").strip().lower()
    if speaker in {"doctor", "patient", "unknown"}:
        return speaker
    return "unknown"


def _speaker_label_for_segment(
    speaker: str, session: ConsultationSession
) -> str:
    if speaker == "doctor":
        return session.doctor.name or "Doctor"
    if speaker == "patient":
        return session.patient.name or "Patient"
    return "Consultation"


def _build_soniox_segments(
    tokens: list[dict[str, Any]], session: ConsultationSession
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for token in tokens:
        if not isinstance(token, dict):
            continue
        text = str(token.get("text", "")).strip()
        if not text:
            continue
        speaker_key = str(token.get("speaker") or "unknown").lower()
        speaker = "doctor" if speaker_key.endswith("1") else "patient" if speaker_key.endswith("2") else "unknown"
        speaker_label = (
            session.doctor.name
            if speaker == "doctor"
            else session.patient.name
            if speaker == "patient"
            else "Speaker"
        )
        start_ms = int(token.get("start_ms") or 0)
        end_ms = int(token.get("end_ms") or start_ms)
        if (
            current is not None
            and current["speaker"] == speaker
            and end_ms - int(current["endedAtSeconds"] * 1000) <= 1500
        ):
            current["text"] = f"{current['text']} {text}".strip()
            current["endedAtSeconds"] = round(end_ms / 1000)
            continue
        current = {
            "id": str(uuid.uuid4()),
            "speaker": speaker,
            "speakerLabel": speaker_label,
            "text": text,
            "startedAtSeconds": round(start_ms / 1000),
            "endedAtSeconds": round(end_ms / 1000),
        }
        segments.append(current)
    if not segments:
        raise TranscriptionProviderError("Soniox transcription returned no tokens.")
    return segments
