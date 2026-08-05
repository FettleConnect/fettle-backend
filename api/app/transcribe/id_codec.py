from __future__ import annotations

import uuid

ENTITY_CODES = {
    "doctor": 0x10,
    "intake_link": 0x11,
    "session": 0x12,
    "audio_artifact": 0x13,
    "document_artifact": 0x14,
    "report_upload": 0x15,
    "report_chunk": 0x16,
    "report_job": 0x17,
    "queue_entry": 0x18,
    "pdf_delivery_job": 0x19,
    "sync_job": 0x1A,
}

CODE_TO_ENTITY = {value: key for key, value in ENTITY_CODES.items()}
_MAX_INTERNAL_ID = (1 << 112) - 1


def encode_public_id(entity: str, internal_id: int) -> str:
    code = ENTITY_CODES[entity]
    if internal_id < 0 or internal_id > _MAX_INTERNAL_ID:
        raise ValueError(f"{entity} id out of supported range")
    payload = f"{code:02x}{internal_id:028x}"
    return str(
        uuid.UUID(
            hex=(
                f"{payload[0:8]}-"
                f"{payload[8:12]}-"
                f"8{payload[12:15]}-"
                f"8{payload[15:18]}-"
                f"{payload[18:30]}"
            )
        )
    )


def decode_public_id(value: str, entity: str) -> int:
    if value.isdigit():
        return int(value)
    parsed = uuid.UUID(value)
    payload = (
        parsed.hex[0:12] + parsed.hex[13:16] + parsed.hex[17:20] + parsed.hex[20:32]
    )
    code = int(payload[0:2], 16)
    if CODE_TO_ENTITY.get(code) != entity:
        raise ValueError(f"invalid {entity} id")
    return int(payload, 0)
