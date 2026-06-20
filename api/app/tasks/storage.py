from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx


def read_from_s3_bucket(bucket: str, key: str) -> dict[str, Any]:
    if key.startswith(("http://", "https://")):
        response = httpx.get(key, timeout=30.0)
        return {
            "bucket": bucket,
            "key": key,
            "content_type": response.headers.get("content-type"),
            "size": len(response.content),
            "body": (
                response.text
                if response.headers.get("content-type", "").startswith("text/")
                else None
            ),
            "body_base64": (
                None
                if response.headers.get("content-type", "").startswith("text/")
                else base64.b64encode(response.content).decode()
            ),
            "status": "ok" if response.is_success else "error",
            "status_code": response.status_code,
        }

    root = Path(os.getenv("ARTIFACT_STORE_PATH", "artifacts")).resolve()
    path = (root / bucket / key).resolve()
    if root not in path.parents and path != root:
        return {"bucket": bucket, "key": key, "status": "invalid_path"}
    if not path.exists() or not path.is_file():
        return {"bucket": bucket, "key": key, "status": "not_found"}

    content = path.read_bytes()
    try:
        body = content.decode()
    except UnicodeDecodeError:
        body = None
    return {
        "bucket": bucket,
        "key": key,
        "size": len(content),
        "body": body,
        "body_base64": None if body is not None else base64.b64encode(content).decode(),
        "status": "ok",
    }
