# Transcribe Route Inventory

This is the phase-1 compatibility inventory for the migrated `Transcribe` backend.

## Health

| Route | Status | Notes |
| --- | --- | --- |
| `GET /health` | `preserve` | Implemented with source-compatible health payload and local mock-mode flag |
| `GET /internal/health` | `preserve` | Implemented with internal API authentication and actor metadata |

## Auth

| Route | Status | Notes |
| --- | --- | --- |
| `GET /doctors` | `preserve` | First Python compatibility implementation added |
| `POST /auth/select` | `preserve` | Implemented as legacy branch returning `PIN_LOGIN_REQUIRED` |
| `POST /auth/pin/login` | `preserve` | First Python compatibility implementation added |
| `POST /auth/pin/change` | `preserve` | Implemented as legacy compatibility PIN-rotation flow |
| `GET /auth/me` | `preserve` | First Python compatibility implementation added |

## Sessions

| Route | Status | Notes |
| --- | --- | --- |
| `POST /sessions` | `preserve` | First Python compatibility implementation added |
| `GET /sessions` | `preserve` | First Python compatibility implementation added |
| `GET /sessions/:sessionId` | `preserve` | First Python compatibility implementation added |
| `GET /sessions/:sessionId/patient-history` | `preserve` | First Python compatibility implementation added |
| `GET /patients/profile-by-phone` | `preserve` | Implemented |
| `PATCH /sessions/:sessionId/case-status` | `preserve` | First Python compatibility implementation added |
| `POST /sessions/:sessionId/audio` | `preserve` | Implemented with filesystem-backed artifact persistence |
| `POST /sessions/:sessionId/transcription` | `preserve` | Implemented with local provider adapter and structured transcript persistence |
| `PUT /sessions/:sessionId/draft-document` | `preserve` | Implemented with persisted draft document storage |
| `POST /sessions/:sessionId/pdf` | `preserve` | Implemented with generated PDF artifact persistence |

## Artifacts

| Route | Status | Notes |
| --- | --- | --- |
| `GET /sessions/:sessionId/artifacts/transcript` | `preserve` | Implemented as compatibility download route |
| `GET /sessions/:sessionId/artifacts/audio` | `preserve` | Implemented as compatibility download route |
| `GET /sessions/:sessionId/artifacts/pdf` | `preserve` | Implemented as compatibility download route |

## Report Context

| Route | Status | Notes |
| --- | --- | --- |
| `POST /sessions/:sessionId/report-context/uploads` | `preserve` | Implemented with storage-backed uploads |
| `GET /sessions/:sessionId/report-context/uploads` | `preserve` | Implemented |
| `GET /sessions/:sessionId/report-context/uploads/:uploadId/content` | `preserve` | Implemented as inline download route |
| `POST /sessions/:sessionId/report-context/extract` | `preserve` | Implemented with deterministic local extraction fallback |
| `POST /sessions/:sessionId/report-context/extract-async` | `preserve` | Implemented with in-process job completion |
| `GET /sessions/:sessionId/report-context/jobs/:jobId` | `preserve` | Implemented |
| `DELETE /sessions/:sessionId/report-context/uploads/:uploadId` | `preserve` | Implemented |
| `GET /sessions/:sessionId/report-context/jobs/:jobId/stream` | `adapt` | Implemented as one-shot SSE compatibility stream |
| `POST /sessions/:sessionId/report-context/jobs/:jobId/cancel` | `preserve` | Implemented |
| `POST /sessions/:sessionId/report-context/suggestions` | `adapt` | Implemented with compatibility-first draft suggestion response |
| `POST /sessions/:sessionId/report-context/apply` | `preserve` | Implemented |

## Public Intake

| Route | Status | Notes |
| --- | --- | --- |
| `GET /public/intake/:token` | `preserve` | Implemented |
| `GET /public/intake/:token/patient-profile` | `preserve` | Implemented |
| `GET /public/intake/by-slug/:departmentSlug/:doctorNameSlug` | `preserve` | Implemented |
| `POST /public/intake/:token` | `preserve` | Implemented with queue dedupe semantics |

## Queue

| Route | Status | Notes |
| --- | --- | --- |
| `GET /intake-link` | `preserve` | Implemented |
| `GET /queue` | `preserve` | Implemented |
| `POST /queue/:queueEntryId/accept` | `preserve` | Implemented |
| `POST /queue/:queueEntryId/no-show` | `preserve` | Implemented |
| `POST /queue/:queueEntryId/cancel` | `preserve` | Implemented |

## Internal Admin

| Route | Status | Notes |
| --- | --- | --- |
| `GET /internal/admin/doctors` | `preserve` | Implemented |
| `GET /internal/admin/doctors/:doctorId` | `preserve` | Implemented |
| `POST /internal/admin/doctors` | `preserve` | Implemented |
| `PUT /internal/admin/doctors/:doctorId` | `preserve` | Implemented |
| `POST /internal/admin/doctors/:doctorId/pin-reset` | `preserve` | Implemented |
| `POST /internal/admin/doctors/:doctorId/pin-lock/clear` | `preserve` | Implemented |
| `GET /internal/admin/intake-links` | `preserve` | Implemented |
| `GET /internal/admin/intake-links/:intakeLinkId` | `preserve` | Implemented |
| `PATCH /internal/admin/intake-links/:intakeLinkId` | `preserve` | Implemented |
| `GET /internal/admin/queue` | `preserve` | Implemented |
| `GET /internal/admin/queue/:queueEntryId` | `preserve` | Implemented |
| `POST /internal/admin/queue/:queueEntryId/accept` | `preserve` | Implemented |
| `POST /internal/admin/queue/:queueEntryId/no-show` | `preserve` | Implemented |
| `POST /internal/admin/queue/:queueEntryId/cancel` | `preserve` | Implemented |
| `GET /internal/admin/sessions` | `preserve` | Implemented with compatibility filters |
| `GET /internal/admin/sessions/:sessionId` | `preserve` | Implemented |
| `GET /internal/admin/sessions/:sessionId/transcription` | `preserve` | Implemented |
| `GET /internal/admin/sessions/:sessionId/artifacts/transcript` | `preserve` | Implemented |
| `GET /internal/admin/sessions/:sessionId/artifacts/audio` | `preserve` | Implemented |
| `GET /internal/admin/sessions/:sessionId/artifacts/pdf` | `preserve` | Implemented |
| `POST /internal/admin/sessions` | `preserve` | Implemented |
| `PUT /internal/admin/sessions/:sessionId/draft-document` | `preserve` | Implemented |
| `POST /internal/admin/sessions/:sessionId/transcription` | `preserve` | Implemented |
| `POST /internal/admin/sessions/:sessionId/pdf` | `preserve` | Implemented |
| `PUT /internal/admin/sessions/:sessionId/pdf-delivery` | `preserve` | Implemented with persisted settings |
| `GET /internal/admin/sessions/:sessionId/pdf-delivery` | `preserve` | Implemented with persisted settings and jobs |
| `POST /internal/admin/sessions/:sessionId/pdf-delivery/send` | `preserve` | Implemented with pending delivery job enqueue |
| `GET /internal/admin/pdf-delivery/jobs` | `preserve` | Implemented |
| `GET /internal/admin/pdf-delivery/jobs/:jobId` | `preserve` | Implemented |

## KPI And Reliability

| Route | Status | Notes |
| --- | --- | --- |
| `GET /public/reliability-kpis` | `preserve` | Implemented |
| `GET /public/kpi/scorecard` | `preserve` | Implemented |
| `GET /public/kpi/providers` | `preserve` | Implemented |
| `GET /public/kpi/pipeline` | `preserve` | Implemented |
| `GET /public/kpi/report-context-fallback` | `preserve` | Implemented |
| `GET /public/kpi/event-trends` | `preserve` | Implemented |
| `GET /internal/admin/reliability/alerts` | `preserve` | Implemented |

## Sync

| Route | Status | Notes |
| --- | --- | --- |
| `GET /internal/admin/fettle-sync/stats` | `adapt` | Implemented with persisted sync-job stats; disabled by default when Fettle Connect is not configured |
| `GET /internal/admin/fettle-sync/jobs` | `adapt` | Implemented with persisted sync-job listing and status filters |
| `POST /internal/admin/fettle-sync/dry-run` | `adapt` | Implemented with local dry-run processing when sync is enabled; returns disabled compatibility error otherwise |
| `POST /internal/admin/fettle-sync/jobs/:jobId/replay` | `adapt` | Implemented with dead-letter replay when sync is enabled; returns disabled compatibility error otherwise |
