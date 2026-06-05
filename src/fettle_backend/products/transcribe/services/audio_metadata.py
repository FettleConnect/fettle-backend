from __future__ import annotations


def detect_audio_mime_type_from_bytes(data: bytes) -> str | None:
    if len(data) < 12:
        return None
    if data[0:4] == bytes([0x1A, 0x45, 0xDF, 0xA3]):
        return "audio/webm"
    if data[0:4] == b"OggS":
        return "audio/ogg"
    if data[0:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if data[0:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    return None


def canonicalize_audio_upload(
    session_id: str,
    file_name: str | None,
    mime_type: str | None,
    duration_seconds: float | None,
    data: bytes,
) -> dict[str, object]:
    canonical_name = (file_name or f"session-{session_id}.webm").strip() or f"session-{session_id}.webm"
    canonical_name = canonical_name.replace("/", "_").replace("\\", "_").replace(":", "_")
    header_mime = (mime_type or "application/octet-stream").split(";")[0].strip()
    detected_mime = detect_audio_mime_type_from_bytes(data)
    resolved_mime = detected_mime or header_mime
    duration = 0.0 if duration_seconds is None else max(float(duration_seconds), 0.0)
    return {
        "fileName": canonical_name,
        "mimeType": resolved_mime,
        "durationSeconds": duration,
    }
