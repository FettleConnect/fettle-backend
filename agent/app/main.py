from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import Body, FastAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

app = FastAPI(title="LiveKit Agent")

# Where to persist lightweight call metadata for local testing
DATA_DIR = Path(os.getenv("LIVEKIT_AGENT_DATA", "/data/livekit"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/dispatch")
def dispatch(payload: dict = Body(...)):
    """Dispatch a phone -> LiveKit room mapping.

    Expected payload: {"customer": {"number": "+911234...", "id_key": "..."}, "metadata": {...}}
    """
    phone = payload.get("customer", {}).get("number")
    id_key = payload.get("customer", {}).get("id_key")
    metadata = payload.get("metadata", {})

    if not phone or not id_key:
        return {"error": 1, "errorMsg": "missing phone or id_key"}

    # Persist a small JSON file for local debugging / downstream processors
    obj = {
        "vapi_id": id_key,
        "phone": phone,
        "metadata": metadata,
    }
    path = DATA_DIR / f"calls_{id_key}.json"
    with open(path, "w") as fh:
        json.dump(obj, fh)

    logging.info("Dispatched call %s -> %s", phone, id_key)
    logging.warning(
        "/dispatch is a local-debug placeholder and does not create a real LiveKit dispatch"
    )

    # Placeholder: integration with LiveKit server / SIP dispatcher should happen here.
    return {"error": 0, "vapi_id": id_key}


@app.post("/create-room")
def create_room(payload: dict = Body(...)):
    name = payload.get("name")
    if not name:
        return {"error": 1, "errorMsg": "name required"}
    # For now simply record the room request
    path = DATA_DIR / f"room_{name}.json"
    with open(path, "w") as fh:
        json.dump(payload, fh)
    return {"error": 0, "room": name}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
