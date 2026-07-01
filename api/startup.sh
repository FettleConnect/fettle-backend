#!/bin/sh
set -e

echo "==> Running Alembic migrations..."
cd /app && python -m alembic upgrade head

echo "==> Starting Uvicorn..."
exec uvicorn app.main:app \
    --host "${API_HOST:-0.0.0.0}" \
    --port "${API_PORT:-8000}" \
    ${RELOAD_FLAG:+$RELOAD_FLAG}
