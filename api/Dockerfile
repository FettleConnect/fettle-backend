FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

COPY api/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && \
    pip install -r requirements.txt

RUN addgroup --system app && adduser --system --ingroup app app

# Copy alembic config + migrations first (for migration step)
COPY --chown=app:app api/alembic.ini ./
COPY --chown=app:app api/alembic ./alembic

# Copy application code
COPY --chown=app:app api/app ./app
COPY --chown=app:app src ./src
COPY --chown=app:app fettle_backend ./fettle_backend

# Create 'api' namespace package so "from api.app.XXX" imports work inside Docker.
# All source files use api.app.* imports (works locally); inside Docker the app
# lives at /app/app/, so we create /app/api/__init__.py + /app/api/app -> /app/app.
RUN mkdir -p /app/api && \
    touch /app/api/__init__.py && \
    ln -s /app/app /app/api/app && \
    chown -R app:app /app/api

# Startup script runs migrations then starts uvicorn
COPY --chown=app:app api/startup.sh ./
RUN chmod +x startup.sh

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["./startup.sh"]
