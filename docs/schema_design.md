# Fettle Unified Multi-Tenant Schema Design

## Overview

The Fettle database uses **column-based multi-tenancy**: a single PostgreSQL schema
where every domain table carries an `organization_id` column. Tenant isolation is
enforced at the application layer — every SQL query WHERE-filters on `organization_id`
derived from the JWT. No row-level-security (RLS) is used to keep query plans simple.

---

## Tenant Foundation

```
organizations
├─ id          UUID PK
├─ name        VARCHAR(255)
├─ slug        VARCHAR(100) UNIQUE INDEX
├─ type        VARCHAR(20)   "hospital" | "corporate" | "clinical"
├─ is_active   BOOLEAN
└─ timestamps

hospital_configs          (1:1 with org, type=hospital)
├─ organization_id        UNIQUE FK → organizations(id) CASCADE
├─ reception_email
├─ pharmacy_email
├─ vobiz_did              E.164 phone — outbound caller ID + inbound DID routing
├─ vobiz_sip_trunk_id     LiveKit SIP trunk ID (pre-provisioned per hospital)
├─ whatsapp_channel_id    Vobiz WABA channel ID
├─ whatsapp_waba_id
├─ agent_prompt_prefix    Custom instructions prepended to AI agent
└─ branding_json          {logo_url, primary_color, broadcast_text}

corporate_configs         (1:1 with org, type=corporate)
├─ organization_id        UNIQUE FK → organizations(id) CASCADE
├─ company_name
├─ industry
├─ s3_bucket
└─ branding_json
```

**Multi-tenant invariant**: `organization_id` is NEVER accepted in request body.
It is ALWAYS extracted from the JWT (`sub`, `organization_id` claims).

---

## Unified Users Table

One `users` table covers all role types. Role-specific fields are nullable columns:

```
users
├─ id               UUID PK
├─ organization_id  FK → organizations CASCADE
├─ email            VARCHAR(255) — UNIQUE per org
├─ password_hash    bcrypt
├─ name
├─ role             "superadmin" | "hospital_admin" | "hospital_user" | "doctor"
│                   | "corporate_admin" | "hr" | "employee"
├─ is_active        BOOLEAN
│
│  ── Hospital user permission flags (perm_*) ──
├─ perm_patient_engagement
├─ perm_community_engagement
├─ perm_revisit_engagement
├─ perm_escalation_engagement
├─ perm_calllog_engagement
├─ perm_upload_engagement
├─ perm_pdf_engagement
│
│  ── Doctor-specific ──
├─ mobile_number
├─ department
├─ availability_json   [{day, slots: [...]}]
├─ timings
├─ consultation_fee    NUMERIC(10,2)
│
│  ── Corporate employee ──
├─ company
├─ corp_department
├─ address
├─ phone_number
├─ profile_picture_url
└─ saved_addresses_json
```

**JWT payload**: `{sub: user_id, email, organization_id, role, exp, iat}`

---

## Hospital Domain

### Patient Data
```
patients
├─ organization_id  FK (TenantMixin)
├─ serial_no        INTEGER
├─ patient_name
├─ age
├─ mobile_no        VARCHAR(20)
├─ department
└─ visit_date       DATE (null = no-date batch upload)

UNIQUE: (organization_id, mobile_no, department, visit_date)

hospital_upload_logs
├─ organization_id
├─ file_names       JSONB ["file1.csv", ...]
├─ status           PENDING | SUCCESS | FAILED
└─ message
```

### Outbound Calls
```
campaigns
├─ organization_id
├─ name, template_type, audience_type
├─ patient_ids      JSONB [uuid, ...]  (targeted patients)
├─ purpose          campaign prompt for AI agent
├─ start_date, end_date
├─ unconnected_only BOOLEAN
└─ status           created | running | paused | completed | failed

outbound_calls
├─ organization_id
├─ patient_id       FK → patients (nullable)
├─ campaign_id      FK → campaigns (nullable)
├─ livekit_call_id  Room name (was vapi_id)
├─ status, ended_reason
├─ calling_process  not_happened | queued | in_progress | connected | not_connected
├─ started_at, ended_at
├─ message_s3_link, audio_s3_link
└─ task_id          Celery task ID

call_feedback
├─ organization_id
├─ patient_id       FK → patients
├─ call_status      connected | not_connected
├─ call_outcome     positive | negative | escalated | no_feedback
├─ remarks
├─ community_added, revisit_encouraged, escalation_required  BOOLEAN
├─ call_duration    INTEGER (seconds)
└─ called_at

escalations
├─ organization_id
├─ patient_id       FK → patients
├─ issue_description
├─ status           pending | in-progress | resolved
├─ department
├─ escalated_at, resolved_at, resolution_notes

community_engagements
├─ organization_id
├─ patient_id       FK → patients
├─ engagement_type  post | comment | poll_participation | like | share
├─ engagement_date
└─ department
```

### Inbound Calls (mirrors outbound, linked to InboundCall not Patient)
```
inbound_calls
├─ organization_id
├─ livekit_call_id   (Vobiz CallUUID)
├─ from_phone_number, to_phone_number
├─ department
├─ calling_process
└─ started_at, ended_at

inbound_feedback      FK → inbound_calls
inbound_escalations   FK → inbound_calls
inbound_community_engagements  FK → inbound_calls
```

### MediVoice (Doctor AI Consultations)
```
medivoice_sessions
├─ organization_id
├─ doctor_id        FK → users (role=doctor)
├─ patient_name, patient_mobile, patient_email
├─ overall_summary, diagnosis
├─ medicines        JSONB [{name, dosage, duration}, ...]
├─ revisit_date, revisit_time
└─ meta_data        JSONB {bp, findings, ...}

medivoice_transcriptions
├─ organization_id
├─ session_id       FK → medivoice_sessions
├─ speaker          doctor | patient
├─ text
└─ timestamp        FLOAT (seconds)
```

### Dermatology Chatbot
```
dermatology_patients
├─ organization_id
├─ email            UNIQUE per org
└─ name

dermatology_threads
├─ organization_id
├─ patient_id       FK → dermatology_patients
├─ name
├─ mode             general_education | post_payment_intake | dermatologist_review | final_output
├─ payment_status   unpaid | paid
├─ status           active | completed
├─ conversation     JSONB [{id, role, content, images?}, ...]
├─ intake_data      JSONB {symptoms, duration, location, meds, history, skin_images, report_images}
└─ draft_response   TEXT (AI-generated doctor draft)
```

---

## Corporate Domain

```
body_compositions         (per employee, per measurement)
wellness_videos           (content library)
wellness_sessions         + session_requests M2M (join interest)
therapists
packages
outdoor_activities        + activity_requests M2M
session_bookings          PENDING | BOOKED | CANCELLED
therapist_bookings
activity_bookings
posts                     (social feed)
user_saved_posts
comments                  (self-referencing for replies via parent_id)
post_likes                UNIQUE(post_id, user_id)
comment_likes             UNIQUE(comment_id, user_id)
notifications
support_tickets           Open | Resolved
```

---

## Shared

```
global_configs
├─ key   VARCHAR(100) PK
└─ value TEXT
```

---

## LiveKit Room Metadata Schema

Every outbound call dispatches a room with this metadata JSON:

```json
{
  "organization_id": "uuid",
  "hospital_name": "City Hospital",
  "agent_prompt_prefix": "Always greet in local language.",
  "type": "outbound",
  "phone_number": "+919876543210",
  "patient_id": "uuid",
  "campaign_id": "uuid",
  "prompt": "Health checkup reminder"
}
```

Inbound rooms get metadata injected by the dispatch rule:

```json
{
  "organization_id": "uuid",
  "hospital_name": "City Hospital",
  "type": "inbound"
}
```

**The agent NEVER hardcodes hospital names or phone numbers.** All tenant context flows through Room Metadata.

---

## Internal API Security Model

| Auth method | Used by | Endpoints |
|---|---|---|
| Bearer JWT | Browser/frontend clients | All `/api/v1/*` except chatbot patient auth |
| Patient JWT (`role=patient`) | Dermatology patient browser | `/api/v1/chatbot/*` (GET/POST) |
| X-Internal-API-Secret | LiveKit agent process | `/api/patient_lookup_by_phone/`, `/api/internal/escalation/`, `/api/v1/tenant/doctors` |

The shared secret is set via `INTERNAL_API_SHARED_SECRET` env var and is NEVER put in JWT.

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Column-based tenancy (not schema/db-per-tenant) | Simplifies migrations, joins, and reporting. 100+ tenants is feasible with proper indexing on `organization_id`. |
| JWT carries `organization_id` | Eliminates need for a `hospital_id` lookup on every request. Frontend cannot spoof a different tenant's data. |
| Unified `users` table | Avoids JOIN sprawl. Doctor, hospital staff, HR, and employee share the same auth flow. Role-specific fields use nullable columns. |
| `patients` merges Patient + Patient_date | `visit_date` nullable. Unique constraint on `(org, mobile, dept, visit_date)` allows both batch and dated imports. |
| `livekit_call_id` replaces `vapi_id` | Reflects actual LiveKit room names. Vobiz correlation sent via `X-VH-CorrelationId` SIP header. |
| Doctor schedules from DB, not static JSON | Agent fetches `GET /api/v1/tenant/doctors` at runtime — always reflects current doctor roster. |
| `branding_json["broadcast_text"]` | Reuses existing JSONB column instead of a separate table for the simple broadcast text feature. |
