from __future__ import annotations

import sys
import types

if __name__ == "app":
    api_package = sys.modules.setdefault("api", types.ModuleType("api"))
    api_package.__path__ = []  # type: ignore[attr-defined]
    api_package.app = sys.modules[__name__]
    sys.modules.setdefault("api.app", sys.modules[__name__])
