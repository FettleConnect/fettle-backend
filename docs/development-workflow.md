# Development Workflow

This branch follows the unified-backend standup rules for verified personal branches.

## Tracking

- Create a GitHub issue before committing implementation work.
- Link commits and PRs back to the issue.
- Use GitHub Projects as the source of truth for assignment and progress state.

## Branches

- Work on a personal branch, for example `snehit`.
- Do not push unverified code.
- Keep commits scoped to the issue being worked on.

## Commit Format

Use Conventional Commit style:

```text
feat(transcribe): migrate session routes
fix(sync): preserve disabled adapter response
test(transcribe): cover report context extraction
```

Allowed commit types are `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `style`, and `test`.

## Local Gates

Install the hooks:

```bash
python -m pip install -e ".[dev]"
pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg
```

Run the full local gate before pushing:

```bash
alembic upgrade head
ruff format --check .
ruff check .
mypy src/fettle_backend
python -m pytest tests/contract -q
```

## Compose Deployment

Use Docker Compose for local deployment:

```bash
docker compose up --build
```

The app service runs migrations before starting FastAPI. PostgreSQL and artifact storage are managed as compose services/volumes.
