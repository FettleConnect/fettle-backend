#!/bin/sh
set -e

python - <<'PY'
import os

import psycopg

database_url = os.environ["DATABASE_URL"]
sync_url = (
    database_url
    .replace("postgresql+psycopg://", "postgresql://")
    .replace("postgresql+psycopg_async://", "postgresql://")
)

with psycopg.connect(sync_url.rsplit("/", 1)[0] + "/postgres", autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'fettle_test'")
        if cur.fetchone() is None:
            cur.execute("CREATE DATABASE fettle_test")
PY

exec "$@"
