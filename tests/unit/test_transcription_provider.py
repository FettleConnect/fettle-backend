from __future__ import annotations

from api.app.transcribe.schemas.common import ConsultationSession
from api.app.transcribe.services.transcription_provider import _extract_openai_segments


def _session() -> ConsultationSession:
    return ConsultationSession.model_validate(
        {
            "id": "12000000-0000-8000-8000-000000000001",
            "doctorId": "8d6b3a30-14fd-4661-b5fc-d24395290391",
            "patient": {
                "name": "Asha",
                "phoneNumber": "9999999999",
                "age": 32,
            },
            "doctor": {
                "name": "Dr. Imran Ul Haq",
                "department": "Cardiology",
            },
            "caseStatus": "open",
            "status": "completed",
            "createdAt": "2026-06-30T08:00:00Z",
            "updatedAt": "2026-06-30T08:00:00Z",
        }
    )


def test_extract_openai_segments_preserves_known_speakers() -> None:
    segments = _extract_openai_segments(
        {
            "segments": [
                {
                    "id": "seg-doctor",
                    "speaker": "doctor",
                    "text": "How are you feeling today?",
                    "start": 0,
                    "end": 3,
                },
                {
                    "id": "seg-patient",
                    "speaker": "patient",
                    "text": "I still have a cough.",
                    "start": 4,
                    "end": 7,
                },
            ]
        },
        _session(),
    )

    assert segments[0]["speaker"] == "doctor"
    assert segments[0]["speakerLabel"] == "Dr. Imran Ul Haq"
    assert segments[1]["speaker"] == "patient"
    assert segments[1]["speakerLabel"] == "Asha"


def test_extract_openai_segments_uses_neutral_label_without_diarization() -> None:
    segments = _extract_openai_segments(
        {"text": "General transcript without diarization."},
        _session(),
    )

    assert segments == [
        {
            "id": segments[0]["id"],
            "speaker": "unknown",
            "speakerLabel": "Consultation",
            "text": "General transcript without diarization.",
            "startedAtSeconds": 0,
            "endedAtSeconds": 0,
        }
    ]
