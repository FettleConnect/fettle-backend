from __future__ import annotations

from datetime import datetime
from typing import Any


def json_audio(
    patient_id: str,
    text: str,
    called_at: datetime | None,
    duration: str | int | float | None,
) -> dict[str, Any]:
    normalized = " ".join(text.split())
    lowered = normalized.lower()
    escalation_required = any(
        word in lowered
        for word in (
            "urgent",
            "emergency",
            "complaint",
            "pain",
            "worse",
            "not resolved",
        )
    )
    revisit_encouraged = any(
        word in lowered
        for word in ("revisit", "follow up", "follow-up", "appointment", "visit again")
    )
    positive = any(
        word in lowered for word in ("better", "good", "improved", "thank", "satisfied")
    )
    negative = any(
        word in lowered
        for word in ("bad", "poor", "angry", "unhappy", "worse", "complaint")
    )

    if escalation_required:
        outcome = "escalated"
    elif positive and not negative:
        outcome = "positive"
    elif negative:
        outcome = "negative"
    else:
        outcome = "no_feedback"

    return {
        "patient_id": patient_id,
        "called_at": called_at.isoformat() if called_at else None,
        "duration": duration,
        "summary": normalized[:500],
        "call_status": "connected" if normalized else "not_connected",
        "call_outcome": outcome,
        "community_added": "community" in lowered,
        "revisit_encouraged": revisit_encouraged,
        "escalation_required": escalation_required,
        "status": "analyzed",
    }
