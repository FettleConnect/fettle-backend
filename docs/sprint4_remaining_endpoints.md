# Sprint 4: Remaining Endpoints & Data Porting

## New Endpoints

### Dashboard
| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/dashboard/roi` | ROI financial + efficiency metrics. `?call_direction=outbound\|inbound`, `?start_date`, `?end_date`. Returns `roi_financial` (Interactions, Appointments Booked, Revenue Influenced ₹, Leakage Prevented ₹) and `roi_efficiency` (Staff Hours Saved, Equivalent FTE Freed, Cost Efficiency Value ₹). |
| `GET /api/v1/dashboard/departments` | Department breakdown: interactions, bookings, conversion rate, revenue. Top intents from community engagement types (fallback: derived from feedback outcomes). |

### Campaign CRUD
| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/campaigns/` | List all campaigns for the org with call stats (total/connected). |
| `DELETE /api/v1/campaigns/{id}` | Delete a campaign (scoped to org). |

### Doctor Management
| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/doctors/` | List all doctors in the org. Optional `?doctor_id=` filter. |
| `POST /api/v1/doctors/` | Create a doctor account (hashed password, role=doctor). Returns `id`. |
| `GET /api/v1/doctors/availability` | List doctor availability slots. Optional `?doctor_id=` or `?doctor_id=all`. |

### WhatsApp Broadcast
| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/calls/broadcast` | Set default broadcast text (saved in HospitalConfig.branding_json). If `target_numbers` provided, sends WhatsApp immediately (fire-and-forget). Supports `template_name` for template messages. |
| `GET /api/v1/calls/broadcast` | Get current default broadcast text for the hospital. |

### MediVoice Extensions
| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/medivoice/sync` | External webhook (protected by `X-Fettle-Secret`). Creates session from external PWA. Auto-triggers prescription notification + reminder scheduling. |
| `GET /api/v1/medivoice/sessions/{id}/artifacts/{type}` | Stream session artifacts: `transcript` (plain text), `audio` (302 redirect to presigned S3 URL), `pdf` (text fallback). |

### Internal API (Agent-Facing, at `/api/` prefix)
| Endpoint | Description |
|----------|-------------|
| `GET /api/patient_lookup_by_phone/` | Lookup patients by phone suffix across all orgs. Protected by `X-Internal-API-Secret`. Returns full call history, previous appointments, feedback, escalations. Used by LiveKit agent for personalized inbound greetings. |

---

## Data Porting Script (`scripts/port_legacy_data.py`)

Idempotent async script to migrate all legacy Django hospital data into the new multi-tenant FastAPI schema.

### Usage
```bash
python scripts/port_legacy_data.py \
    --legacy-dsn "postgresql+psycopg://user:pass@host:5432/legacy_hospital_db" \
    --target-dsn "postgresql+psycopg://postgres:postgres@localhost:5432/fettle_dev" \
    [--dry-run]
```

### Migration Map
| Legacy Table | New Table | Key Change |
|---|---|---|
| `app_hospital_model` | `organizations` + `hospital_configs` | `type='hospital'`, slug generated from name |
| `app_hospital_user_model` | `users` | `role='hospital_user'`, permission flags preserved |
| `app_admin_model` | `users` | `role='hospital_admin'` |
| `app_doctor_model` | `users` | `role='doctor'`, availability/fee preserved |
| `app_patient_model` | `patients` | `organization_id` mapped from legacy hospital_id |
| `app_patient_date_model` | `patients` | Merged: `visit_date` populated |
| `app_hospitaluploadlog` | `hospital_upload_logs` | Direct port |
| `app_campaign` | `campaigns` | Direct port |
| `phone_calling_outbound_hospital` | `outbound_calls` | `vapi_id` → `livekit_call_id` |
| `phone_calling_inbound_hospital` | `inbound_calls` | `to_phone_numnber` typo corrected |
| `app_callfeedbackmodel` | `call_feedback` | Direct port |
| `app_escalationmodel` | `escalations` | Direct port |
| `app_communityengagementmodel` | `community_engagements` | Direct port |
| `app_medivoicesession` | `medivoice_sessions` | `doctor_id` mapped to new users table |
| `app_medivoicetranscription` | `medivoice_transcriptions` | Direct port |

### Idempotency
All inserts use `ON CONFLICT DO NOTHING`. Original UUIDs are preserved where possible.
Running the script multiple times is safe.

### Dry-Run Mode
`--dry-run` executes all queries but rolls back at the end. Use to validate mappings before committing.

---

## Security Notes
- `X-Internal-API-Secret` is set via `INTERNAL_API_SHARED_SECRET` env var
- Broadcast text stored in `HospitalConfig.branding_json["broadcast_text"]`
- Doctor passwords are bcrypt-hashed on creation
- All new endpoints enforce `organization_id` scoping from JWT
