from __future__ import annotations


def import_model_modules() -> None:
    from api.app.models.hospital import transcribe as _transcribe_models  # noqa: F401

