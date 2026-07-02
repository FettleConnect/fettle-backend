from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from api.app.core.config import Settings
from api.app.transcribe.services.auth import AuthService
from api.app.transcribe.services.sessions import SessionService


@dataclass
class SonioxRealtimeToken:
    text: str
    start_ms: int
    end_ms: int
    speaker: str
    is_final: bool


@dataclass
class LiveTranscriptionState:
    live_session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started: bool = False
    doctor_name: str = "Doctor"
    patient_name: str = "Patient"
    preview_final_tokens: list[SonioxRealtimeToken] = field(default_factory=list)
    preview_active_tokens: list[SonioxRealtimeToken] = field(default_factory=list)
    finalized_audio_ms: int = 0
    soniox_socket: Any | None = None
    soniox_listener_task: asyncio.Task | None = None
    keepalive_task: asyncio.Task | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    mock_preview_segments: list[dict[str, Any]] = field(default_factory=list)


class LiveTranscriptionService:
    def __init__(
        self,
        auth_service: AuthService,
        session_service: SessionService,
        settings: Settings,
    ) -> None:
        self.auth_service = auth_service
        self.session_service = session_service
        self.settings = settings

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        state = LiveTranscriptionState()
        try:
            while True:
                message = await websocket.receive()
                if "text" in message and message["text"] is not None:
                    await self._handle_text_message(
                        websocket, state, str(message["text"])
                    )
                    continue
                if "bytes" in message and message["bytes"] is not None:
                    await self._handle_audio_bytes(
                        websocket, state, bytes(message["bytes"])
                    )
        except WebSocketDisconnect:
            return
        finally:
            await self._cleanup(state)

    async def _handle_text_message(
        self, websocket: WebSocket, state: LiveTranscriptionState, raw: str
    ) -> None:
        payload = _safe_json_loads(raw)
        if not isinstance(payload, dict):
            return
        message_type = str(payload.get("type") or "").strip()
        if message_type == "start":
            if state.started:
                return
            await self._handle_start(websocket, state, payload)
            return
        if not state.started:
            return
        if message_type == "pause":
            if state.soniox_socket is not None and state.keepalive_task is None:
                state.keepalive_task = asyncio.create_task(
                    self._keepalive_loop(state.soniox_socket)
                )
            return
        if message_type == "resume":
            await self._stop_keepalive(state)
            return
        if message_type == "stop":
            await self._stop_keepalive(state)
            if state.soniox_socket is not None:
                await state.soniox_socket.send(b"")
            elif state.mock_preview_segments:
                await self._send_json(
                    websocket,
                    {
                        "type": "preview",
                        "segments": [
                            {**segment, "isFinal": True}
                            for segment in state.mock_preview_segments
                        ],
                    },
                    state,
                )

    async def _handle_start(
        self,
        websocket: WebSocket,
        state: LiveTranscriptionState,
        payload: dict[str, Any],
    ) -> None:
        access_token = str(payload.get("accessToken") or "").strip()
        session_id = str(payload.get("sessionId") or "").strip()
        if not access_token or not session_id:
            return
        doctor = (await self.auth_service.current_doctor(access_token)).doctor
        session = await self.session_service.get_session(session_id, doctor.id)
        state.started = True
        state.doctor_name = session.doctor.name
        state.patient_name = session.patient.name
        if self.settings.soniox_api_key:
            state.soniox_socket = await websockets.connect(
                self.settings.soniox_rt_ws_url,
                max_size=None,
            )
            await state.soniox_socket.send(
                json.dumps(
                    {
                        "api_key": self.settings.soniox_api_key,
                        "model": self.settings.soniox_rt_model,
                        "audio_format": "auto",
                        **(
                            {"language_hints": self.settings.soniox_language_hints}
                            if self.settings.soniox_language_hints
                            else {}
                        ),
                        "context": {
                            "general": [
                                {"key": "domain", "value": "Healthcare"},
                                {"key": "topic", "value": "Doctor consultation"},
                                {"key": "doctor", "value": session.doctor.name},
                                {"key": "patient", "value": session.patient.name},
                            ]
                        },
                        "enable_speaker_diarization": True,
                        "enable_language_identification": True,
                    }
                )
            )
            state.soniox_listener_task = asyncio.create_task(
                self._pump_soniox_messages(websocket, state)
            )
        else:
            state.mock_preview_segments = _build_mock_live_preview(session)
        await self._send_json(
            websocket,
            {"type": "ready", "liveSessionId": state.live_session_id},
            state,
        )
        if state.mock_preview_segments:
            await self._send_json(
                websocket,
                {"type": "preview", "segments": state.mock_preview_segments},
                state,
            )

    async def _handle_audio_bytes(
        self, websocket: WebSocket, state: LiveTranscriptionState, data: bytes
    ) -> None:
        if not state.started:
            return
        if state.soniox_socket is not None:
            await state.soniox_socket.send(data)
            return
        if state.mock_preview_segments:
            await self._send_json(
                websocket,
                {"type": "preview", "segments": state.mock_preview_segments},
                state,
            )

    async def _pump_soniox_messages(
        self, websocket: WebSocket, state: LiveTranscriptionState
    ) -> None:
        assert state.soniox_socket is not None
        try:
            async for raw in state.soniox_socket:
                if isinstance(raw, bytes):
                    continue
                payload = _safe_json_loads(raw)
                if not isinstance(payload, dict):
                    continue
                tokens = [
                    SonioxRealtimeToken(
                        text=str(token.get("text") or ""),
                        start_ms=int(token.get("start_ms") or 0),
                        end_ms=int(token.get("end_ms") or token.get("start_ms") or 0),
                        speaker=str(token.get("speaker") or ""),
                        is_final=bool(token.get("is_final")),
                    )
                    for token in payload.get("tokens", [])
                    if isinstance(token, dict)
                ]
                if tokens:
                    _update_preview_state(state, tokens)
                    await self._send_json(
                        websocket,
                        {
                            "type": "preview",
                            "segments": _build_preview_segments(state),
                        },
                        state,
                    )
                if payload.get("finished"):
                    break
        finally:
            if state.soniox_socket is not None:
                await state.soniox_socket.close()
                state.soniox_socket = None

    async def _keepalive_loop(self, soniox_socket: Any) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                await soniox_socket.send(json.dumps({"type": "keepalive"}))
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _send_json(
        self,
        websocket: WebSocket,
        payload: dict[str, Any],
        state: LiveTranscriptionState,
    ) -> None:
        async with state.send_lock:
            await websocket.send_json(payload)

    async def _stop_keepalive(self, state: LiveTranscriptionState) -> None:
        if state.keepalive_task is None:
            return
        state.keepalive_task.cancel()
        try:
            await state.keepalive_task
        except asyncio.CancelledError:
            pass
        state.keepalive_task = None

    async def _cleanup(self, state: LiveTranscriptionState) -> None:
        await self._stop_keepalive(state)
        if state.soniox_listener_task is not None:
            state.soniox_listener_task.cancel()
            try:
                await state.soniox_listener_task
            except asyncio.CancelledError:
                pass
        if state.soniox_socket is not None:
            await state.soniox_socket.close()
            state.soniox_socket = None


def _safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _update_preview_state(
    state: LiveTranscriptionState,
    tokens: list[SonioxRealtimeToken],
) -> None:
    final_tokens = [
        token
        for token in tokens
        if token.is_final and token.end_ms > state.finalized_audio_ms
    ]
    if final_tokens:
        state.preview_final_tokens.extend(final_tokens)
        state.finalized_audio_ms = max(
            state.finalized_audio_ms,
            max(token.end_ms for token in final_tokens),
        )
    state.preview_active_tokens = [
        token
        for token in tokens
        if not token.is_final and token.end_ms >= state.finalized_audio_ms
    ]


def _build_preview_segments(state: LiveTranscriptionState) -> list[dict[str, Any]]:
    return _tokens_to_segments(
        state.preview_final_tokens,
        state.doctor_name,
        state.patient_name,
        True,
    ) + _tokens_to_segments(
        state.preview_active_tokens,
        state.doctor_name,
        state.patient_name,
        False,
    )


def _tokens_to_segments(
    tokens: list[SonioxRealtimeToken],
    doctor_name: str,
    patient_name: str,
    is_final: bool,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for token in tokens:
        text = token.text.strip()
        if not text:
            continue
        speaker_key = token.speaker
        speaker, speaker_label = _speaker_details(
            speaker_key, doctor_name, patient_name
        )
        if current is not None and current["speaker"] == speaker:
            current["text"] = (
                f"{current['text']}{_join_token_text(current['text'], text)}"
            )
            current["endedAtSeconds"] = round(token.end_ms / 1000, 3)
            continue
        current = {
            "id": str(uuid.uuid4()),
            "speaker": speaker,
            "speakerLabel": speaker_label,
            "text": text,
            "startedAtSeconds": round(token.start_ms / 1000, 3),
            "endedAtSeconds": round(token.end_ms / 1000, 3),
            "isFinal": is_final,
        }
        segments.append(current)
    return segments


def _speaker_details(
    speaker_key: str, doctor_name: str, patient_name: str
) -> tuple[str, str]:
    normalized = speaker_key.lower()
    if normalized.endswith("1"):
        return "doctor", doctor_name
    if normalized.endswith("2"):
        return "patient", patient_name
    if speaker_key:
        return "unknown", f"Speaker {speaker_key}"
    return "unknown", "Speaker"


def _join_token_text(existing: str, token_text: str) -> str:
    if not existing:
        return token_text
    if existing.endswith((" ", "\n")) or token_text.startswith(
        (".", ",", "!", "?", ":", ";")
    ):
        return token_text
    existing_tail = existing.rstrip()
    next_head = token_text.lstrip()
    if not existing_tail or not next_head:
        return token_text
    previous_char = existing_tail[-1]
    current_char = next_head[0]
    if previous_char.isalpha() and current_char.isalpha():
        if len(existing_tail) == 1 or existing_tail.endswith(("'", "-", "/")):
            return token_text
        return f" {token_text}"
    return f" {token_text}"


def _build_mock_live_preview(session) -> list[dict[str, Any]]:
    patient_label = session.patient.name or "Patient"
    doctor_label = session.doctor.name or "Doctor"
    return [
        {
            "id": str(uuid.uuid4()),
            "speaker": "doctor",
            "speakerLabel": doctor_label,
            "text": f"Good morning {patient_label}. Tell me what has been bothering you today.",
            "startedAtSeconds": 0,
            "endedAtSeconds": 5,
            "isFinal": False,
        },
        {
            "id": str(uuid.uuid4()),
            "speaker": "patient",
            "speakerLabel": patient_label,
            "text": "I have had a persistent cough, throat irritation, and mild fever for the last three days.",
            "startedAtSeconds": 5,
            "endedAtSeconds": 11,
            "isFinal": False,
        },
    ]
