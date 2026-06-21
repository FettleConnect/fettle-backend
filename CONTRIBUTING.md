# Contributing

This backend is meant to serve multiple clients:

- the versioned FastAPI API under `/api/v1`
- legacy hospital frontend routes under `/api` and selected root aliases
- the PWA transcript frontend contract from `/workspaces/pwa-plan`

Route changes should keep those surfaces distinct. New product work should usually
go into `/api/v1`; compatibility work should be clearly marked and tested against
the client contract it supports.

## Adding Routes

1. Add the handler in the closest existing module under `api/app/views/`.
   - Hospital domain routes live in `api/app/views/v1/hospital/`.
   - Auth routes live in `api/app/views/v1/auth.py`.
   - Chatbot and dermatology routes live in `api/app/views/v1/chatbot.py`.
   - Compatibility routes live in a dedicated compat module.
2. Register the router in `api/app/main.py`.
3. Keep the mounted path intentional:
   - `/api/v1/...` for current public API routes.
   - `/api/...` for old hospital frontend compatibility.
   - root-level `/sessions`, `/doctors`, `/auth/*`, `/public/*`, and `/queue/*`
     only for PWA compatibility routes.
4. If a route exists only to support an older client, add a short `# LEGACY`
   comment near the route or handler.

Do not put a new route in a compatibility router unless the path, method, request
body, and response shape are dictated by an existing client.

## Response Shapes

Response compatibility means more than returning similar data. The browser code
may validate exact keys and nested objects.

- Use Pydantic response models for new `/api/v1` routes when possible.
- For legacy hospital routes, preserve the old top-level keys such as `error`,
  `msg`, `data`, `history`, or `token`.
- For PWA transcript routes, match the DTOs in
  `/workspaces/pwa-plan/packages/shared/src/index.ts`.
- Do not rename fields when adding compatibility routes. Add mapping code at the
  edge instead.
- Preserve file response behavior for artifacts, including content type,
  `Content-Disposition`, redirects, and inline preview options.

## PWA Transcript API Surface

The PWA frontend calls root-level Express-style routes. Current equivalent
business logic under `/api/v1/medivoice` is not enough unless the root route and
DTO match the PWA contract.

Minimum PWA transcript surface to support:

- `GET /doctors`
- `POST /auth/select`
- `POST /auth/pin/login`
- `POST /auth/pin/change`
- `GET /auth/me`
- `POST /sessions`
- `GET /sessions`
- `GET /sessions/{sessionId}`
- `POST /sessions/{sessionId}/audio`
- `POST /sessions/{sessionId}/transcription`
- `PUT /sessions/{sessionId}/draft-document`
- `POST /sessions/{sessionId}/pdf`
- `GET /sessions/{sessionId}/artifacts/transcript`
- `GET /sessions/{sessionId}/artifacts/audio`
- `GET /sessions/{sessionId}/artifacts/pdf`
- `GET /sessions/{sessionId}/patient-history`
- `PATCH /sessions/{sessionId}/case-status`
- `/sessions/{sessionId}/report-context/*`
- `/sessions/{sessionId}/pdf-delivery/*`
- `/public/intake/*`
- `/queue/*`
- `/public/kpi/*`
- `/public/reliability-kpis`
- `/internal/admin/*`

For these routes, use the shared PWA schemas as the contract source of truth:

- `loginDoctorResponseSchema`
- `authMeResponseSchema`
- `listDoctorsResponseSchema`
- `createSessionResponseSchema`
- `listSessionsResponseSchema`
- `getSessionResponseSchema`
- `uploadSessionAudioResponseSchema`
- `submitSessionTranscriptionResponseSchema`
- `updateDraftDocumentResponseSchema`
- `generateSessionPdfResponseSchema`
- `getPatientHistoryResponseSchema`
- report-context and PDF-delivery schemas

## Auth And Tenant Rules

- Never accept `hospital_id` or `organization_id` from a public request body when
  the authenticated token can provide it.
- Use `get_current_user` for authenticated API routes.
- Use a separate internal authentication dependency for internal admin routes.
- Keep public intake routes intentionally unauthenticated, but validate their
  tokens or slugs before exposing any data.

## Persistence And Tasks

- Prefer existing SQLAlchemy models in `api/app/models/`.
- Put async background work in `api/app/worker/tasks.py` or a domain-specific
  task module under `api/app/tasks/`.
- Compatibility handlers can delegate to current handlers, but only when the
  response shape still matches the compatibility contract.

## Issue Guidance For PWA Parity

When filing PWA parity work, include:

- the exact PWA route path and method
- the shared schema name from `pwa-plan/packages/shared/src/index.ts`
- the closest existing backend route, if any
- whether the work is route-only, response-mapping, or missing business logic
- transcript/audio/PDF artifact expectations
- acceptance criteria covering both route availability and response shape
