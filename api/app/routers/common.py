from __future__ import annotations

from enum import Enum
from typing import Sequence

from fastapi import APIRouter


def _route_name(name: str, path: str) -> str:
    slug = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return f"{name}_{slug}" if slug else name


def register_json_response(
    router: APIRouter, path: str, name: str, *, tags: Sequence[str | Enum]
) -> None:
    @router.get(path, name=_route_name(name, path), tags=list(tags))
    def endpoint() -> dict[str, str]:
        return {"route": path, "view": name, "status": "ok"}
