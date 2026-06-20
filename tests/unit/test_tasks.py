from __future__ import annotations

"""Unit tests for Celery task helpers — no DB or LiveKit needed."""

import json


class TestJsonAudioAnalysis:
    def test_json_audio_returns_dict(self) -> None:
        from unittest.mock import MagicMock, patch

        # Mock OpenRouter response
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(
            {
                "call_status": "connected",
                "call_outcome": "positive",
                "remarks": "Patient was happy",
                "issue_description": "",
                "called_by": "CloudConnect Agent",
                "community_added": False,
                "revisit_encouraged": True,
                "escalation_required": False,
            }
        )

        with patch("api.app.worker.tasks._openrouter_client") as mock_client:
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[mock_choice]
            )
            from api.app.worker.tasks import json_audio

            result = json_audio(
                "patient-uuid-123",
                "agent: Hello\npatient: Hi",
                "2024-01-01T10:00:00",
                2.5,
            )

        assert result["call_status"] == "connected"
        assert result["call_outcome"] == "positive"
        assert result["patient_id"] == "patient-uuid-123"
        assert result["call_duration"] == 2.5

    def test_json_audio_handles_invalid_json(self) -> None:
        from unittest.mock import MagicMock, patch

        mock_choice = MagicMock()
        mock_choice.message.content = "not json at all"

        with patch("api.app.worker.tasks._openrouter_client") as mock_client:
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[mock_choice]
            )
            from api.app.worker.tasks import json_audio

            result = json_audio("pid", "text", "2024-01-01", 1.0)

        assert result.get("error") == 1

    def test_json_audio_handles_api_exception(self) -> None:
        from unittest.mock import patch

        with patch("api.app.worker.tasks._openrouter_client") as mock_client:
            mock_client.chat.completions.create.side_effect = Exception("API down")
            from api.app.worker.tasks import json_audio

            result = json_audio("pid", "text", "2024-01-01", 1.0)

        assert result.get("error") == 1


class TestRoomNameClassification:
    def test_inbound_room_detected(self) -> None:
        from api.app.worker.tasks import _is_inbound_room

        assert _is_inbound_room("inbound_+911234567890_abc123") is True
        assert (
            _is_inbound_room("out_patient__hospital__phone__20240101_livekit") is False
        )

    def test_outbound_room_not_inbound(self) -> None:
        from api.app.worker.tasks import _is_inbound_room

        assert _is_inbound_room("outreach_some-uuid-here") is False
        assert _is_inbound_room("some_random_room") is False


class TestMetadataBuilder:
    def test_build_outbound_metadata(self) -> None:
        from api.app.worker.tasks import build_room_metadata

        meta = build_room_metadata(
            organization_id="org-uuid-123",
            hospital_name="City Hospital",
            agent_prompt_prefix="Always speak in English.",
            call_type="outbound",
            phone_number="+919876543210",
            patient_id="patient-uuid-456",
            prompt="Health checkup campaign",
        )
        parsed = json.loads(meta)
        assert parsed["hospital_name"] == "City Hospital"
        assert parsed["organization_id"] == "org-uuid-123"
        assert parsed["agent_prompt_prefix"] == "Always speak in English."
        assert parsed["type"] == "outbound"
        assert "Amor" not in meta  # Ensure no hardcoded hospital names
