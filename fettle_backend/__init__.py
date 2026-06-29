from __future__ import annotations

from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent.parent / "src" / "fettle_backend"
__path__ = [str(_SRC_PATH)]
