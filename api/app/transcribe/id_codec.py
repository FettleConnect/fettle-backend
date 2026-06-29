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


def encode_public_id(entity: str, internal_id: int) -> str:
    code = ENTITY_CODES[entity]
    value = (code << 120) | internal_id
    return str(uuid.UUID(int=value))


def decode_public_id(value: str, entity: str) -> int:
    if value.isdigit():
        return int(value)
    parsed = uuid.UUID(value)
    code = parsed.int >> 120
    if CODE_TO_ENTITY.get(code) != entity:
        raise ValueError(f"invalid {entity} id")
    return parsed.int & ((1 << 120) - 1)
