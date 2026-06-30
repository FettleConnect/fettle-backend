from __future__ import annotations

import uuid
from typing import Any

from api.app.schemas.hospital.transcribe import ConsultationSession


class LocalTranscriptionProvider:
    provider_name = "mock-gemini"

    async def transcribe(self, session: ConsultationSession) -> dict[str, Any]:
        duration = max(
            30,
            round(
                session.audioArtifact.durationSeconds if session.audioArtifact else 90
            ),
        )
        midpoint = max(12, round(duration / 2))
        doctor_label = "Doctor"
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
                "testsPrescribed": [{"name": "CBC"}],
                "nextSteps": [
                    "Continue hydration and rest.",
                    "Return for review if symptoms worsen.",
                ],
                "cautions": ["Seek urgent help if shortness of breath develops."],
            },
        }
