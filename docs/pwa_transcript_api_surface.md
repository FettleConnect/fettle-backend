# PWA Transcript API Surface

This is the compatibility target for unifying `/workspaces/pwa-plan` with this
backend. The PWA expects root-level routes and DTOs from
`/workspaces/pwa-plan/packages/shared/src/index.ts`.

## Current State

The current backend is mostly organized around:

- `/api/v1/...` for new FastAPI routes
- `/api/...` for legacy hospital compatibility
- `/api/v1/medivoice/...` for the current Medivoice implementation

The PWA does not call those paths. It calls root-level routes such as
`/sessions`, `/doctors`, `/auth/select`, and `/public/intake/*`.

## Required Route Groups

Auth and doctors:

- `GET /doctors`
- `POST /auth/select`
- `POST /auth/pin/login`
- `POST /auth/pin/change`
- `GET /auth/me`

Sessions and transcript workflow:

- `POST /sessions`
- `GET /sessions`
- `GET /sessions/{sessionId}`
- `POST /sessions/{sessionId}/audio`
- `POST /sessions/{sessionId}/transcription`
- `PUT /sessions/{sessionId}/draft-document`
- `POST /sessions/{sessionId}/pdf`
- `GET /sessions/{sessionId}/patient-history`
- `PATCH /sessions/{sessionId}/case-status`

Artifacts:

- `GET /sessions/{sessionId}/artifacts/transcript`
- `GET /sessions/{sessionId}/artifacts/audio`
- `GET /sessions/{sessionId}/artifacts/pdf`

Report context:

- `POST /sessions/{sessionId}/report-context/uploads`
- `DELETE /sessions/{sessionId}/report-context/uploads/{uploadId}`
- `GET /sessions/{sessionId}/report-context/uploads`
- `POST /sessions/{sessionId}/report-context/extract`
- `POST /sessions/{sessionId}/report-context/extract-async`
- `POST /sessions/{sessionId}/report-context/suggestions`
- `POST /sessions/{sessionId}/report-context/apply`

PDF delivery:

- `PUT /sessions/{sessionId}/pdf-delivery`
- `GET /sessions/{sessionId}/pdf-delivery`
- `POST /sessions/{sessionId}/pdf-delivery/send`

Public intake and queue:

- `GET /intake-link`
- `GET /queue`
- `POST /queue/{queueEntryId}/accept`
- `POST /queue/{queueEntryId}/no-show`
- `POST /queue/{queueEntryId}/cancel`
- `GET /public/intake/{token}`
- `GET /public/intake/by-slug/{departmentSlug}/{doctorNameSlug}`
- `POST /public/intake/{token}`

Observability and internal admin:

- `GET /public/reliability-kpis`
- `GET /public/kpi/scorecard`
- `GET /public/kpi/providers`
- `GET /public/kpi/pipeline`
- `/internal/admin/*`

## Response Contract

Use the PWA shared schemas as the source of truth. Important schemas include:

- `listDoctorsResponseSchema`
- `loginDoctorResponseSchema`
- `pinLoginResponseSchema`
- `authMeResponseSchema`
- `createSessionResponseSchema`
- `listSessionsResponseSchema`
- `getSessionResponseSchema`
- `uploadSessionAudioResponseSchema`
- `submitSessionTranscriptionResponseSchema`
- `updateDraftDocumentResponseSchema`
- `generateSessionPdfResponseSchema`
- `getPatientHistoryResponseSchema`
- report-context schemas
- PDF-delivery schemas

Compatibility is not complete until the root route returns data that those
schemas can parse.

## Implementation Guidance

- Add a dedicated PWA compatibility router instead of mixing these root paths
  into hospital legacy handlers.
- Keep route functions thin: map PWA DTOs to existing models/services, then map
  backend data back into PWA DTOs.
- If existing `/api/v1/medivoice` logic is reused, adapt the response at the
  route edge. Do not change `/api/v1/medivoice` responses just to satisfy PWA.
- Preserve artifact download behavior:
  - transcript should download as the PWA expects
  - audio should stream or redirect consistently
  - PDF should support attachment and inline preview when requested
- Add tests for exact route/method availability and exact top-level response
  keys before implementing deeper business behavior.

## Suggested Issue Acceptance Criteria

- All root-level PWA transcript routes are registered.
- Auth routes return PWA doctor DTOs and bearer tokens.
- Session routes return `{ "session": ... }` or `{ "sessions": [...] }` shapes
  matching the PWA shared schemas.
- Transcript, audio, and PDF artifact endpoints return expected headers.
- Report-context endpoints either implement the PWA flow or return explicit
  structured errors while the missing backend pieces are built.
- Tests cover route availability, auth behavior, and response parsing against
  representative PWA DTOs.
