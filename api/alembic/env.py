from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Ensure the api package root is on sys.path when running alembic directly ─
# Add two levels up (fettle-backend/) so "api" package is importable outside Docker.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Support both:
#   - Outside Docker (fettle-backend/): from api.app.* import ...
#   - Inside Docker (/app/):            from app.* import ...
try:
    import api.app.models  # noqa: F401, E402
    from api.app.core.config import settings  # noqa: E402
    from api.app.db.base import Base  # noqa: E402
except ModuleNotFoundError:
    import app.models  # type: ignore[no-redef]  # noqa: F401, E402
    from app.core.config import settings  # type: ignore[no-redef]  # noqa: E402
    from app.db.base import Base  # type: ignore[no-redef]  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Prefer DATABASE_URL from env; fall back to alembic.ini setting."""
    return settings.sync_database_url or config.get_main_option("sqlalchemy.url", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL without a live DB connection."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a live synchronous DB connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
