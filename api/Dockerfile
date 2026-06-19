FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy alembic config + migrations first (for migration step)
COPY alembic.ini ./
COPY alembic ./alembic

# Copy application code
COPY app ./app

# Create 'api' namespace package so "from api.app.XXX" imports work inside Docker.
# All source files use api.app.* imports (works locally); inside Docker the app
# lives at /app/app/, so we create /app/api/__init__.py + /app/api/app -> /app/app.
RUN mkdir -p /app/api && \
    touch /app/api/__init__.py && \
    ln -s /app/app /app/api/app

# Startup script runs migrations then starts uvicorn
COPY startup.sh ./
RUN chmod +x startup.sh

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["./startup.sh"]
