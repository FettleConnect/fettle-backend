FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY pyproject.toml ./
COPY docs/transcribe-backend-migration-plan.md docs/transcribe-backend-migration-plan.md
COPY src src

RUN pip install .

COPY alembic.ini alembic.ini
COPY alembic alembic

RUN mkdir -p /app/var/artifacts && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "fettle_backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
