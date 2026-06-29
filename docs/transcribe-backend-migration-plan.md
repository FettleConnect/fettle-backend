# Transcribe Backend Migration Plan

## Scope

This document defines the local-first migration plan for moving the `fettleTranscribe` backend into `fettle-backend` as the first product slice of a unified Fettle backend.

This plan is only for the `Transcribe` backend slice.
Other backend consolidations will be owned by other people.

## Locked Decisions

- Destination repo: `fettle-backend`
- Target architecture: modular monolith
- Source behavior: `fettleTranscribe`
- Destination stack: `FastAPI + SQLAlchemy + Alembic + Pydantic + pytest`
- Runtime style: async-first
- Database: PostgreSQL
- Local database: PostgreSQL container
- Storage: artifact metadata in PostgreSQL, binary files on filesystem through a shared storage adapter
- Auth: shared auth/org boundary, with doctor-selection login preserved through a legacy compatibility adapter
- Internal admin auth: shared-secret style for phase 1
- Job execution: in-process worker style
- Fettle sync: disabled by default in phase 1, with persisted job adapter/admin routes implemented
- Compatibility target: preserve current `fettleTranscribe` API behavior as closely as possible
- Migration order: dependency-first
- Delivery mode now: local work on branch `snehit`
- Delivery mode later: clean issue-linked branches and PRs
- Final output for this phase: code + schema + tests + documented gaps/handoffs

## Non-Goals

- Full cross-product backend consolidation in this sprint
- Production data migration from MongoDB
- Final shared identity rollout across all products
- End-to-end downstream `Fettle sync` integration
- Final object-storage design
- Full runtime bring-up of every integration during this planning pass

## Success Criteria

At the end of this migration phase, `fettle-backend` should contain:

- a Python/FastAPI modular-monolith skeleton
- shared platform module boundaries needed by `Transcribe`
- a Postgres schema for the `Transcribe` slice
- migrated `Transcribe` backend routes and service boundaries
- compatibility-oriented request/response contracts
- automated tests across schema, services, API contracts, and targeted integrations
- explicit documentation of what is implemented, what is tested, and what remains blocked or deferred

## Source Of Truth

- Behavioral source of truth: `/home/snehit/fettle/fettleTranscribe`
- Destination architecture source of truth: `/home/snehit/fettle/fettle-backend`
- Other repos may inform later shared-platform work, but they do not redefine `Transcribe` migration behavior during this phase

## Proposed Repository Layout

```text
docs/
  transcribe-backend-migration-plan.md
src/
  app/
    main.py
    config.py
  platform/
    auth/
    orgs/
    storage/
    jobs/
    observability/
    db/
  products/
    transcribe/
      api/
      domain/
      services/
      repositories/
      schemas/
      jobs/
tests/
  unit/
  integration/
  contract/
alembic/
docker/
```

## Shared Platform Modules Required On Day 1

### `platform.auth`

- token issuing and verification
- legacy doctor-selection compatibility adapter
- internal admin shared-secret guard

### `platform.orgs`

- org/workspace concepts
- product-specific membership hooks
- enough structure to avoid hard-coding legacy doctor identity as the permanent platform model

### `platform.storage`

- artifact/file metadata model
- file read/write abstraction
- local filesystem-backed implementation for phase 1

### `platform.jobs`

- in-process job claiming/execution boundary
- retry/backoff primitives
- job persistence model
- enough abstraction to support `Transcribe` jobs and disabled-by-default `Fettle sync`

### `platform.observability`

- KPI event write/read boundary
- summary/replay logic
- metric naming and section grouping support

## `Transcribe` Product Modules

The `Transcribe` slice should own:

- doctor directory behavior
- auth compatibility flow
- consultation/session lifecycle
- patient history lookup
- case status behavior
- audio uploads
- transcription orchestration
- report-context extraction
- draft document editing
- transcript/audio/pdf artifacts
- public intake flows
- internal admin flows
- KPI/reliability APIs and persistence
- Fettle sync boundary with persisted jobs and admin controls, but without a live downstream Connect dependency in phase 1

## API Compatibility Strategy

Phase 1 should preserve route paths and payload behavior as closely as practical so the current frontend can switch with minimal changes.

The route inventory must classify every current route as:

- `preserve`
- `adapt`
- `stub`
- `drop`

Default rule for this migration: preserve unless there is a clear reason not to.

## Route Inventory Baseline

### Auth

- `GET /doctors`
- `POST /auth/select`
- `GET /auth/me`

Status target: `preserve`

### Sessions

- `POST /sessions`
- `GET /sessions`
- `GET /sessions/:sessionId`
- `GET /sessions/:sessionId/patient-history`
- `PATCH /sessions/:sessionId/case-status`
- `POST /sessions/:sessionId/audio`
- `POST /sessions/:sessionId/transcription`
- `PUT /sessions/:sessionId/draft-document`
- `POST /sessions/:sessionId/pdf`

Status target: `preserve`

### Artifacts

- `GET /sessions/:sessionId/artifacts/transcript`
- `GET /sessions/:sessionId/artifacts/audio`
- `GET /sessions/:sessionId/artifacts/pdf`

Status target: `preserve`

### Public Intake

- `GET /public/intake/:token`
- `POST /public/intake/:token`
- `GET /public/intake/:token/patient-profile`
- `GET /public/intake/by-slug/:departmentSlug/:doctorNameSlug`

Status target: `preserve`

### Internal Admin

- doctor admin routes
- intake-link admin routes
- queue admin routes
- session admin routes
- PDF delivery routes
- Fettle sync admin routes

Status target: `preserve`/`adapt`, with downstream sync disabled unless Fettle Connect settings are configured

### KPI And Reliability

- `GET /public/reliability-kpis`
- `GET /public/kpi/scorecard`
- `GET /public/kpi/providers`
- `GET /public/kpi/pipeline`
- `GET /public/kpi/report-context-fallback`
- `GET /public/kpi/event-trends`

Status target: `preserve`

### Sync

- sync-triggering behavior after transcription or draft changes
- sync job stats and replay admin endpoints

Status target: job model and admin surface `preserve`/`adapt`; downstream execution disabled unless Fettle Connect settings are configured

## Data Migration Strategy

There is no production-data constraint for this phase.
That means the Postgres schema should be designed cleanly for long-term maintainability instead of mirroring MongoDB documents.

### Schema Rules

- preserve behavior, not document shape
- normalize shared platform concepts
- keep `Transcribe` domain tables explicit
- use denormalized fields only where they make compatibility or query behavior materially simpler
- keep file metadata separate from file binary contents
- keep jobs and KPI events persisted explicitly

## Initial Postgres Mapping

This is the table-by-table starting map for the `Transcribe` slice.

### Shared Platform Tables

- `users`
- `organizations`
- `workspaces`
- `user_organization_memberships`
- `stored_files`
- `job_definitions` or `jobs`
- `job_attempts`
- `kpi_events`

### `Transcribe` Tables

- `transcribe_doctors`
- `transcribe_intake_links`
- `transcribe_sessions`
- `transcribe_session_audio`
- `transcribe_transcriptions`
- `transcribe_report_context_documents`
- `transcribe_report_context_items`
- `transcribe_draft_documents`
- `transcribe_artifacts`
- `transcribe_pdf_delivery_jobs`
- `transcribe_queue_entries`
- `transcribe_sync_jobs`

## Mongo Concept To Postgres Table Mapping

### Doctors

- Mongo concept: seeded doctor repository
- Target: `transcribe_doctors`
- Notes: keep slug/addressing behavior for public intake and legacy auth compatibility

### Sessions

- Mongo concept: consultation session repository
- Target: `transcribe_sessions`
- Notes: include patient identifiers, doctor ownership, case status, timestamps, compatibility fields

### Audio Metadata

- Mongo concept: uploaded audio stored against session state
- Target: `transcribe_session_audio` + `stored_files`
- Notes: metadata in relational tables, binary file in storage adapter

### Transcription State

- Mongo concept: transcription state inside session-oriented documents
- Target: `transcribe_transcriptions`
- Notes: keep idempotency and processing-state behavior explicit

### Draft Documents

- Mongo concept: editable consultation draft document
- Target: `transcribe_draft_documents`

### Report Context

- Mongo concept: report-context progress and extracted items on session records
- Target: `transcribe_report_context_documents` + `transcribe_report_context_items`

### Artifacts

- Mongo concept: generated transcript/PDF/audio artifact references
- Target: `transcribe_artifacts` + `stored_files`

### KPI Events

- Mongo concept: `kpi_events`
- Target: `kpi_events`
- Notes: preserve replay-at-boot behavior

### Sync Jobs

- Mongo concept: `fettle_sync_jobs`
- Target: `transcribe_sync_jobs` or shared `jobs` rows tagged by product/job type
- Notes: preserve queue state shape, but downstream execution can remain disabled

## Authentication Strategy

### External Doctor Auth

- keep `GET /doctors`, `POST /auth/select`, and `GET /auth/me`
- implement as a legacy compatibility layer on top of shared auth/org boundaries
- do not let doctor-selection become the permanent platform identity model

### Internal Admin Auth

- preserve shared-secret style for phase 1
- keep internal API controls separate from browser-facing auth

## Storage Strategy

### Database

- PostgreSQL is the canonical app database
- local development should use a PostgreSQL container

### Files

- file metadata belongs in PostgreSQL
- binary content should live on the filesystem in phase 1
- all file access should go through a shared storage adapter

## Job Strategy

### Phase 1

- use a shared job boundary
- keep execution in-process
- implement real `Transcribe` jobs
- keep downstream `Fettle sync` disabled by default while preserving the job adapter

### Jobs That Should Exist

- transcription job flow
- report-context extraction job flow
- PDF/artifact generation job flow
- sync job model with disabled-by-default adapter

## Observability Strategy

- preserve current public KPI/reliability route surface
- preserve KPI event persistence and replay semantics
- implement behind shared `platform.observability`
- keep `Transcribe` metric groupings compatible with current sections:
  - `auth.*`
  - `session.*`
  - `delivery.*`
  - `report_context.*`
  - `fettle_sync.*`
  - `http.*`

## Testing Strategy

Tests should be split into four layers.

### 1. Schema And Model Tests

- model constraints
- enum/state transitions
- relational integrity
- migration smoke checks

### 2. Service And Repository Tests

- session lifecycle behavior
- patient history scoping
- case-status boundary behavior
- artifact persistence behavior
- KPI event replay behavior

### 3. API Contract Tests

- compatibility coverage for current route shapes
- auth compatibility
- session and artifact APIs
- public intake APIs
- internal admin APIs
- KPI/reliability APIs

### 4. Targeted Integration Tests

- transcription flow orchestration
- report-context extraction flow
- artifact generation flow
- job retry/state transitions
- sync disabled-by-default and enabled dry-run behavior

## Implemented Vs Tested Vs Blocked Tracking

Every phase deliverable should be marked in one of these states:

- `implemented`
- `tested`
- `manual smoke still needed`
- `integration-blocked`
- `deferred`

This distinction must remain explicit throughout the branch and in handoff notes.

## Four-Day Execution Plan

### Day 1: Skeleton And Reference Capture

- copy the current `fettleTranscribe` backend into this branch as reference material only
- scaffold Python project structure
- add FastAPI, SQLAlchemy, Alembic, Pydantic, pytest, and local container setup
- create shared module boundaries
- define initial route inventory
- define initial Postgres schema map

Exit criteria:

- repo has working skeleton and migration reference
- route inventory exists
- schema direction is fixed

### Day 2: Schema And Core Platform

- implement DB base, session management, and Alembic baseline
- create shared auth, org, storage, jobs, and observability boundaries
- implement `Transcribe` core models and repositories
- document Mongo-to-Postgres mapping in code and plan

Exit criteria:

- major tables and migrations exist
- shared module seams are stable enough for product code

### Day 3: Core `Transcribe` APIs And Services

- implement auth compatibility routes
- implement core sessions flow
- implement patient-history and case-status behavior
- implement audio, transcription, draft-document, and artifact service boundaries
- implement public intake and internal admin surfaces

Exit criteria:

- full route surface is represented in Python, with external integrations disabled by default when not configured

### Day 4: Jobs, Observability, Tests, And Handoff

- implement job flows for transcription, report context, and PDF generation
- preserve KPI/reliability routes and event persistence/replay
- keep sync adapter disabled by default and test the enabled dry-run path
- add tests across all four layers
- write explicit handoff notes and remaining gaps
- prepare issue/branch breakdown for clean delivery branches

Exit criteria:

- code + schema + tests exist
- blocked items are clearly named
- cleanup split for delivery branches is defined

## Proposed Delivery Issues

These are suggested issues for the eventual clean delivery flow.

1. `Bootstrap FastAPI modular monolith and shared platform boundaries in fettle-backend`
2. `Design and migrate Transcribe Postgres schema with Alembic baseline`
3. `Migrate Transcribe auth and session compatibility APIs`
4. `Migrate Transcribe artifact, transcription, and report-context flows`
5. `Migrate Transcribe public intake and internal admin surfaces`
6. `Migrate Transcribe KPI/reliability and job persistence behavior`
7. `Add compatibility and integration tests for Transcribe backend migration`

## Proposed Delivery Branches

- `feat/platform-bootstrap`
- `feat/transcribe-schema-baseline`
- `feat/transcribe-core-api`
- `feat/transcribe-artifacts-ai`
- `feat/transcribe-admin-observability`
- `feat/transcribe-test-coverage`

## Commit And PR Workflow

### Local Now

- use `snehit` as the local working branch
- allow temporary reference imports and messy intermediate commits locally
- do not treat local branch history as final delivery history

### Delivery Later

- create issues before ship-intent commits
- split clean work into issue-linked branches
- use consistent commit and PR naming
- keep each PR scoped to one logical migration slice

## Repo Reading Checklist

Before or during implementation, review:

- `fettleTranscribe/docs/api.md`
- `fettleTranscribe/docs/architecture.md`
- `fettleTranscribe/apps/api`
- `fettleTranscribe/apps/web/src/api.ts`
- `fettleTranscribe` tests covering API behavior
- any doctor/intake/admin/KPI-specific route code in `apps/api`

## Coordination And Handoffs

### Can Decide Now

- `Transcribe` module boundaries
- Python stack
- Postgres schema direction
- route inventory strategy
- local storage strategy
- test strategy
- local branch workflow

### Needs SOP Or Team Confirmation

- final issue and PR templates if they are organization-specific
- branch naming rules if already standardized elsewhere
- merge ordering with other backend owners
- final shared-platform naming if there is an org-wide convention

### Safe To Implement Before SOP

- modular layout
- schema baseline
- compatibility routes
- storage adapter
- job abstraction
- observability abstraction
- tests

### Must Wait Before Merge

- anything depending on org-wide branch/issue/PR rules that are not yet confirmed
- final integration expectations with other backend owners
- any route or model changes that need explicit cross-team approval

## Known Gaps And Risks

- `Fettle sync` does not require Fettle Connect to run in this phase; the adapter is implemented, disabled by default, and only end-to-end downstream HTTP delivery remains environment-dependent
- broader unified-backend platform rules may evolve while `Transcribe` migration is underway
- preserving full API compatibility without a route matrix and tests will fail; this is not optional
- a raw copy of the TypeScript API is useful as reference, but should not become the architecture
- if shared modules become too abstract too early, the migration will stall; keep them minimal and `Transcribe`-driven

## Immediate Next Steps

1. Create the Python project skeleton and local Postgres container setup.
2. Import the existing `fettleTranscribe` backend into this branch as reference material only.
3. Build the route inventory and mark each route `preserve`, `adapt`, `stub`, or `drop`.
4. Create the initial Alembic baseline and core Postgres tables.
5. Start the `Transcribe` slice with auth/session compatibility flows first.
