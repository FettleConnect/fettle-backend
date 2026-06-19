# Sprint 2 API Documentation

## Overview

Sprint 2 delivers the core hospital API layer: authentication, patient management, feedback/escalation/community workflows, and both outbound and inbound dashboard analytics. All endpoints are prefixed `/api/v1/`.

---

## Authentication

All protected endpoints require a Bearer JWT in the `Authorization` header. The `organization_id` is **always derived from the token** — never accepted in request body.

### `POST /api/v1/auth/login`
Unified login for all user types (hospital_admin, hospital_user, doctor, hr, employee).

**Request:**
```json
{"email": "user@hospital.com", "password": "SecurePass123!"}
```

**Response:**
```json
{
  "error": 0,
  "token": "<jwt>",
  "role": "hospital_user",
  "name": "Dr Smith",
  "organization_id": "<uuid>",
  "user_id": "<uuid>",
  "permissions": {"patient_engagement": true, "calllog_engagement": false, ...}
}
```

### `GET /api/v1/auth/validate`
Validates Bearer JWT, returns user identity.

### `GET /api/v1/auth/tab-access`
Returns permission flags for the current user (relevant for hospital_user/doctor roles).

---

## Tenant

### `GET /api/v1/tenant/branding`
Returns hospital config: branding JSON (logo_url, primary_color), agent_prompt_prefix, vobiz_did, whatsapp_channel_id.

---

## Patients

### `GET /api/v1/patients/`
List patients for the authenticated organization with call status.

**Query params:**
- `call_status` — Comma-separated filter: `connected`, `not_connected`, `in_progress`, `queued`
- `start_date`, `end_date` — Date range for `uploaded_at` filtering (`YYYY-MM-DD`)
- `department` — Filter by department
- `limit` — Integer, `all`, or `latest` (patients uploaded within 5 min of most recent batch)
- `patient_ids` — Comma-separated UUIDs

**Performance:** Call status resolved via PostgreSQL `DISTINCT ON` subquery — zero Python-side loops.

### `POST /api/v1/patients/upload`
Upload CSV/XLSX patient data. Flexible column name mapping handles `Patient Name`, `Name`, `Mobile No`, `Mobile`, `Phone`, etc.

### `GET /api/v1/patients/{patient_id}/history`
Returns sorted call history for a patient (outbound + inbound calls with presigned S3 links).

### `GET /api/v1/patients/upload-logs`
Lists all CSV/XLSX upload logs for the organization.

---

## Feedback, Escalation & Community

All endpoints enforce tenant isolation — patient/inbound_call must belong to the authenticated org.

### Outbound (linked to Patient)
- `POST /api/v1/calls/feedback` — Log call outcome (call_status, call_outcome, community_added, revisit_encouraged, escalation_required)
- `POST /api/v1/escalations/feedback` — Create escalation + WhatsApp admin notification (fire-and-forget)
- `POST /api/v1/escalations/update` — Update escalation status/resolution; sets `resolved_at` when status = "resolved"
- `GET /api/v1/escalations/` — List all escalations for org
- `POST /api/v1/calls/community` — Log community engagement event
- `POST /api/v1/calls/community/update` — Update community engagement type/department

### Inbound (linked to InboundCall)
- `POST /api/v1/inbound/feedback` — Log inbound call outcome; cascades community + escalation records if flagged
- `POST /api/v1/inbound/escalation` — Create inbound escalation
- `POST /api/v1/inbound/escalation/update` — Resolve inbound escalation
- `POST /api/v1/inbound/community` — Log inbound community engagement

---

## Dashboard — Outbound Analytics

**Performance mandate:** All aggregations are SQL-native using `func.count`, `func.avg`, `func.date_trunc`, `func.extract`. No Python-side loops for data aggregation.

### `GET /api/v1/dashboard/kpi`
4-card KPI summary: Total Patients Contacted, Call Answer Rate, Community Conversion, Escalated Issues. Each card includes week-over-week trend comparison.

**Query params:** `start_date`, `end_date`

### `GET /api/v1/dashboard/admin`
Hospital-wide counts: patients, calls, escalations, connected calls, community added, call answer rate, community conversion rate.

### `GET /api/v1/dashboard/patient-engagement`
Contact volume trend (monthly if >60d range, daily otherwise) + call answer rate pie + metadata.

### `GET /api/v1/dashboard/community-engagement`
12-week community growth chart, department engagement bar, feedback distribution with top-10 patients per outcome, patient intents, conversion metrics.

### `GET /api/v1/dashboard/revisit-analytics`
Department revisit distribution, monthly revisit trend, time-gap histogram (0-7d, 8-30d, 1-3m, 3-6m, 6+m), average revisit gap.

### `GET /api/v1/dashboard/escalation-engagement`
Department-wise escalation counts with issue details, resolution status donut, 5 most recent escalations, metadata (avg resolution time in minutes, resolved today).

### `GET /api/v1/dashboard/recent-activity`
Latest 5 call feedbacks, escalations, community engagements.

---

## Dashboard — Inbound Analytics

All inbound analytics mirror outbound but operate on `InboundCall`, `InboundFeedback`, `InboundEscalation`, `InboundCommunityEngagement` tables.

### `GET /api/v1/inbound/kpi`
8-card KPI: Total Interactions, Avg Resolution (seconds), Inbound Conversion, No-Show Rate, Inbound Traffic, Inbound Answer Rate, Escalated Issues, Revenue Influenced (booked × ₹650).

### `GET /api/v1/inbound/patient-engagement`
Same structure as outbound patient-engagement.

### `GET /api/v1/inbound/community-engagement`
Same structure as outbound community-engagement.

### `GET /api/v1/inbound/escalation-engagement`
Same structure as outbound escalation-engagement.

### `GET /api/v1/inbound/revisit-analytics`
Revisit patterns based on `to_phone_number` + `department` groups in InboundCall.

---

## Multi-Tenancy Security Invariants

1. **JWT encodes `organization_id`** — extracted by `get_current_user` dependency.
2. **`get_current_tenant` returns `organization_id`** from token, never from request body.
3. **All SQL queries WHERE on `organization_id`** — a user from org A cannot see org B's data even if they guess a patient UUID.
4. **Patient upload** — `organization_id` taken from token, never from the uploaded CSV.
5. **Escalation notifications** — fire-and-forget via `asyncio.ensure_future`, never block request completion.

---

## Error Responses

| Status | Meaning |
|--------|---------|
| 401    | Invalid/expired JWT, wrong credentials |
| 401/403 | Missing Bearer token (FastAPI version-dependent) |
| 404    | Resource not found in your organization |
| 422    | Request body validation failure (Pydantic) |

Legacy format preserved: `{"error": 0, ...}` for success, `{"error": 1, "msg": "..."}` for failures where applicable.
