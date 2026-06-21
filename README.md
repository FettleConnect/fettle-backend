# Fettle Backend Scaffold

This repository is set up as a two-service Docker Compose workspace:

- `api/` contains a FastAPI application.
- `agent/` contains a LiveKit agent runtime scaffold.

## Run locally

1. Copy `.env.example` to `.env` and fill in the required values.
2. Build and start the stack:

```bash
docker compose up --build
```

## Run tests

For the usual local workflow, run pytest from the repository root:

```bash
pytest
```

If PostgreSQL is reachable, the test fixture creates the configured test
database automatically. Set `TEST_DATABASE_URL` to point at a non-default test
database.

The API tests live at the repository root while the runtime API image is built
from `api/`, so use the test service when you want the whole test environment
inside Docker:

```bash
docker compose --profile test run --rm api-test
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for route conventions, compatibility
rules, and validation guidance. PWA transcript compatibility work should also
use [docs/pwa_transcript_api_surface.md](docs/pwa_transcript_api_surface.md) as
the route and response checklist.

## Services

- API: `http://localhost:8000`
- Health check: `GET /health`
- Celery worker: runs from the API image and uses Redis as broker/result backend

## Task Layout

The task layer lives under [api/app/tasks/](api/app/tasks) and is split by concern:

- [campaigns.py](api/app/tasks/campaigns.py) for outbound campaign orchestration.
- [outbound.py](api/app/tasks/outbound.py) for outbound dispatch and post-processing.
- [inbound.py](api/app/tasks/inbound.py) for inbound webhook follow-up.
- [medivoice.py](api/app/tasks/medivoice.py) for prescription notifications and reminders.
- [analysis.py](api/app/tasks/analysis.py) and [storage.py](api/app/tasks/storage.py) for shared helpers.
- [redis_state.py](api/app/tasks/redis_state.py) for outbound concurrency tracking.

## Production Deployment

The repository includes two GitHub Actions deploy workflows:

- [.github/workflows/deploy-staging.yml](.github/workflows/deploy-staging.yml) runs on pushes to `main` and deploys to staging.
- [.github/workflows/deploy-prod.yml](.github/workflows/deploy-prod.yml) runs on published releases and deploys to production.

Staging uses [deploy/staging/docker-compose.staging.yml](deploy/staging/docker-compose.staging.yml), which includes a temporary Postgres database and LiveKit SIP. Production uses [deploy/prod/docker-compose.prod.yml](deploy/prod/docker-compose.prod.yml), which also includes LiveKit SIP.

Required GitHub secrets include the production SSH details and the LiveKit/OpenAI keys used by the runtime.
