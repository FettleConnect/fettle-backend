from __future__ import annotations


def import_model_modules() -> None:
    # Importing model modules ensures SQLAlchemy metadata is populated before
    # migrations or table inspection run.
    from fettle_backend.platform.jobs import models as _job_models  # noqa: F401
    from fettle_backend.platform.observability import models as _observability_models  # noqa: F401
    from fettle_backend.platform.storage import models as _storage_models  # noqa: F401
    from fettle_backend.products.transcribe.domain import models as _transcribe_models  # noqa: F401
