# Sprint 3: Celery Workers, LiveKit Dispatch & Agent Migration

## Overview
Sprint 3 delivers the call orchestration backbone: Celery tasks, multi-tenant LiveKit SIP dispatch, Vobiz/LiveKit webhook handlers, outbound campaign triggers, MediVoice session management, and the unified LiveKit agent migrated to the new multi-tenant architecture.

---

## Celery Worker (`api/app/worker/tasks.py`)

All tasks use `asgiref.sync.async_to_sync` to call async SQLAlchemy from the synchronous Celery context. No Django ORM anywhere.

### Tasks

| Task | Description |
|------|-------------|
| `run_campaign_task(campaign_id)` | Iterates campaign patients, waits on concurrency semaphore (Redis), dispatches `call_outbound_task` per patient |
| `call_outbound_task(payload)` | Creates `OutboundCall` record, dispatches via LiveKit SIP using tenant-specific `vobiz_did` and `sip_trunk_id` |
| `process_outbound_calls(payload)` | Pulls S3 transcript, AI-analyzes via OpenRouter, creates `CallFeedback` + `Escalation` records |
| `process_inbound_calls(payload)` | Same as above but for inbound calls, creates `InboundFeedback` + `InboundCommunityEngagement` + `InboundEscalation` |
| `send_prescription_notifications(session_id)` | Sends MediVoice consultation summary to patient via Vobiz WhatsApp |
| `reminder_task(session_id, reminder_type)` | Sends revisit reminder (24h / 1h) via WhatsApp |
| `schedule_reminder_calls(session_id)` | Schedules 24h and 1h reminder tasks with ETA |

### Concurrency Control
- Redis key: `active_outbound_calls_count` (auto-expiry: 300s)
- `CALL_CONCURRENCY_LIMIT` from config (default: 3)
- `run_campaign_task` polls Redis and waits with 10s backoff

### Room Metadata Schema
```json
{
  "organization_id": "uuid",
  "hospital_name": "City Hospital",
  "agent_prompt_prefix": "Always greet in local language.",
  "type": "outbound | inbound",
  "phone_number": "+919876543210",
  "patient_id": "uuid",
  "campaign_id": "uuid",
  "prompt": "Health checkup reminder campaign"
}
```
**Invariant:** `hospital_name` is NEVER hardcoded — always from Room Metadata.

---

## LiveKit Service (`api/app/services/livekit.py`)

### `dispatch_outbound_call()`
- Resolves outbound SIP trunk by `sip_trunk_id` (from `HospitalConfig`) or falls back to name `"Vobiz_Outbound_V5"`
- Passes `vobiz_did` as `X-Caller-DID` SIP header → patients see the hospital's official number
- Embeds full Room Metadata (org, hospital name, prompt prefix) so agent knows the tenant

### `ensure_infrastructure()`
- Per-hospital SIP provisioning: creates `Inbound_{DID}` trunk + `Route_{DID}` dispatch rule
- Room prefix: `inbound_{DID}_` with Room Metadata containing `organization_id`
- Called at agent startup via `infrastructure_guard()`

### `dispatch_outbound_call_sync()`
- Synchronous wrapper for use in Celery tasks (manages own event loop)

---

## Webhook Handlers (`api/app/api/v1/hospital/webhooks.py`)

### `POST /api/v1/webhooks/livekit`
- `room_finished` → decrements Redis counter → dispatches `process_inbound_calls` or `process_outbound_calls`
- Inbound detection: room name starts with `inbound_`

### `POST /api/v1/webhooks/vobiz/inbound` (and `/inbound/`)
- **DID-based org lookup**: queries `HospitalConfig.vobiz_did = To` — no hardcoded numbers
- Creates `InboundCall` record linked to the resolved `organization_id`
- Unknown DIDs return `acknowledged` without creating a record

### `POST /api/v1/webhooks/vobiz/outbound` (and `/outbound/`)
- Updates `OutboundCall.calling_process` via `X-VH-CorrelationId` → `livekit_call_id`
- Status mapping: `completed→connected`, `busy|no-answer|failed|cancel→not_connected`

### `POST /api/v1/webhooks/vobiz/whatsapp` (and `/whatsapp/`)
- Logs delivery status and incoming messages for observability

---

## Outbound Call API (`api/app/api/v1/hospital/calls.py`)

### `POST /api/v1/calls/outbound`
- `is_individual=true` + `patient_ids` → queues individual `call_outbound_task` per patient
- `is_individual=false` → creates `Campaign` record (or uses existing `campaign_id`) + dispatches `run_campaign_task`
- All calls use tenant's `vobiz_did` as caller ID

### `POST /api/v1/calls/outbound/process`
- Manually trigger `process_outbound_calls` for a specific call payload (admin use)

### `GET /api/v1/inbound/`
- Lists inbound calls for the authenticated organization with date-range filter

### `GET /api/v1/calls/export`
- Returns Excel (XLSX) export of outbound calls with patient name, mobile, status, reason, date

---

## MediVoice Routes (`api/app/api/v1/hospital/medivoice.py`)

Doctor-scoped session management for AI-assisted consultations.

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/medivoice/sessions` | Create consultation session |
| `GET /api/v1/medivoice/sessions` | List doctor's sessions |
| `GET /api/v1/medivoice/sessions/{id}` | Get session + transcripts |
| `PATCH /api/v1/medivoice/sessions/{id}` | Update diagnosis, medicines, revisit date |
| `POST /api/v1/medivoice/sessions/{id}/transcript` | Add utterance |
| `POST /api/v1/medivoice/sessions/{id}/notify` | Send WhatsApp prescription + schedule reminders |

---

## Doctor Schedule API

### `GET /api/v1/tenant/doctors?organization_id={uuid}`
- Protected by `X-Internal-API-Secret` (used by LiveKit agent, not JWT)
- Returns doctor list from `User` table (role=doctor) with schedule, fees, department
- Agent replaces hardcoded `doctors.json` file with this live DB query

---

## LiveKit Agent (`fettle-backend/agent/app/agent.py`)

### Multi-Tenant Migration
- **ZERO hardcoded hospital references** — all from Room Metadata
- `hospital_name` → greeting templates, instructions
- `agent_prompt_prefix` → appended to base instructions
- `organization_id` → doctor schedule fetches, escalation routing

### Doctor Schedule
- Fetched from `GET /api/v1/tenant/doctors?organization_id={org_id}` via `X-Internal-API-Secret`
- No static `doctors.json` file needed

### Inbound Greeting
- Attempts patient lookup via `GET /api/patient_lookup_by_phone/` 
- Builds personalized greeting if previous appointments found
- Falls back to "Could you tell me your name?" if lookup fails

### WhatsApp (send_appointment_confirmation)
- Uses per-tenant `whatsapp_channel_id` from `/api/v1/tenant/branding`

### infrastructure_guard()
- Fetches all active hospitals from `/api/v1/tenant/hospitals`
- For each hospital with a `vobiz_did`, calls `ensure_infrastructure()` to provision SIP trunks + dispatch rules

---

## Configuration Changes

New env vars in `.env.dev`:
```
INTERNAL_API_SHARED_SECRET=<shared secret for agent↔API>
INTERNAL_API_BASE_URL=http://api:8000
```

New settings in `core/config.py`:
- `internal_api_shared_secret`
- `internal_api_base_url`

---

## Security Invariants

1. **No hardcoded phone numbers** — DID lookup is DB-driven via `HospitalConfig.vobiz_did`
2. **No hardcoded hospital names** — all from JWT or Room Metadata
3. **Internal API protected** by `X-Internal-API-Secret` (agent-to-backend calls)
4. **Celery task payload** never contains credentials — only IDs that are resolved server-side
5. **Concurrency** enforced via Redis (`active_outbound_calls_count`, auto-expiry 300s)
