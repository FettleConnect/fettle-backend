from __future__ import annotations

import uuid
from pathlib import Path

from fettle_backend.app.config import get_settings


class LocalStorage:
    def __init__(self, root: str | None = None) -> None:
        configured_root = root or get_settings().artifact_storage_root
        self.root = Path(configured_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, namespace: str, file_name: str, data: bytes) -> str:
        safe_name = file_name.replace("/", "_").replace("\\", "_").replace("\x00", "_")
        directory = self.root / namespace
        directory.mkdir(parents=True, exist_ok=True)
        storage_key = f"{namespace}/{uuid.uuid4()}-{safe_name}"
        target = self.root / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return storage_key

    def read_bytes(self, storage_key: str) -> bytes:
        target = self.root / storage_key
        return target.read_bytes()
