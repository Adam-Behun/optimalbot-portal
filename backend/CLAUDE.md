# Backend CLAUDE.md

FastAPI backend for healthcare portal. Handles authentication, patient CRUD, and call orchestration to voice AI bots.

## API Endpoints

### Authentication (`/auth`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/auth/login` | Direct tenant login → JWT | No |
| POST | `/auth/logout` | Logout (audit only) | No |
| POST | `/auth/request-reset` | Generate password reset token | No |
| POST | `/auth/reset-password` | Reset password with token | No |
| POST | `/auth/login-central` | Marketing site login → handoff token | No |
| POST | `/auth/exchange-token` | Exchange handoff token → JWT | No |

### Patients (`/patients`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/patients` | List patients (optional `?workflow=`) | Yes |
| GET | `/patients/{id}` | Get single patient | Yes |
| POST | `/patients` | Create patient | Yes |
| POST | `/patients/bulk` | Bulk create (max 1000) | Yes |
| PUT | `/patients/{id}` | Update patient | Yes |
| DELETE | `/patients/{id}` | Delete patient | Yes |

### Calls (Dial-Out)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/start-call` | Initiate dial-out call | Yes |
| GET | `/call/{session_id}/status` | Get call status | Yes |
| GET | `/call/{session_id}/transcript` | Get call transcript | Yes |
| DELETE | `/call/{session_id}` | End call session | Yes |

### Dial-In
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/dialin-webhook/{client_name}/{workflow_name}` | Daily.co incoming call webhook | No* |

*Dial-in webhook is unauthenticated but validates org slug exists.

### Sessions (`/sessions`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/sessions` | List sessions (filterable) | Yes |
| GET | `/sessions/{session_id}` | Get session detail | Yes |
| DELETE | `/sessions/{session_id}` | End/terminate session | Yes |

### Metrics (`/metrics`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/metrics/summary` | Call metrics summary | Yes |
| GET | `/metrics/breakdown/status` | Status breakdown | Yes |
| GET | `/metrics/breakdown/errors` | Error breakdown | Yes |
| GET | `/metrics/daily` | Daily metrics | Yes |

### Webhooks (`/webhooks`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/webhooks` | List webhooks | Yes |
| POST | `/webhooks` | Create webhook | Yes |
| GET | `/webhooks/{webhook_id}` | Get webhook | Yes |
| PUT | `/webhooks/{webhook_id}` | Update webhook | Yes |
| DELETE | `/webhooks/{webhook_id}` | Delete webhook | Yes |
| POST | `/webhooks/{webhook_id}/test` | Test webhook | Yes |
| POST | `/webhooks/{webhook_id}/reset` | Reset webhook secret | Yes |

### SMS
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/sms-webhook/inbound` | Inbound SMS webhook | No |
| POST | `/sms/send` | Send SMS | Yes |

### Admin (`/admin`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/admin/dashboard` | Admin dashboard stats | Yes |
| GET | `/admin/calls` | Admin call list | Yes |
| GET | `/admin/calls/{session_id}` | Admin call detail | Yes |

### Admin Onboarding (`/admin/onboarding`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/admin/onboarding/upload` | Upload onboarding files | Yes |
| GET | `/admin/onboarding/status/{org}/{workflow}` | Onboarding status | Yes |
| POST | `/admin/onboarding/transcribe/{org}/{workflow}` | Transcribe recordings | Yes |
| GET | `/admin/onboarding/conversations/{org}/{workflow}` | List conversations | Yes |
| GET | `/admin/onboarding/conversations/detail/{id}` | Conversation detail | Yes |
| POST | `/admin/onboarding/conversations` | Create conversation | Yes |
| PUT | `/admin/onboarding/conversations/{id}` | Update conversation | Yes |
| POST | `/admin/onboarding/conversations/{id}/approve` | Approve conversation | Yes |
| DELETE | `/admin/onboarding/conversations/{id}` | Delete conversation | Yes |

### MFA (`/auth/mfa`)
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/auth/login/mfa` | MFA login verification | No |
| POST | `/auth/mfa/setup` | Start MFA setup | Yes |
| POST | `/auth/mfa/verify` | Verify MFA setup | Yes |
| POST | `/auth/mfa/disable` | Disable MFA | Yes |
| GET | `/auth/mfa/status` | Get MFA status | Yes |
| POST | `/auth/mfa/backup-codes` | Generate backup codes | Yes |

## MongoDB Access

Connect to the database from terminal:
```bash
mongo-alfons
```

This opens an interactive mongosh session. Useful commands:
```javascript
show collections                          // List all collections
db.organizations.find({}, {slug: 1})      // List org slugs
db.patients.find({workflow: "eligibility_verification"}).limit(5)  // Sample patients
db.patients.countDocuments({workflow: "eligibility_verification"}) // Count by workflow
```

One-liner queries:
```bash
mongosh "$MONGO_URI" --eval "db.patients.countDocuments()"
```

## MongoDB Collections

### `patients`
Dynamic schema per workflow. Common fields:
- `_id`, `organization_id`, `workflow`
- `call_status`: Not Started | Dialing | In Progress | Completed | Failed | Supervisor Dialed | Voicemail
- `call_transcript`, `last_call_session_id`, `last_call_timestamp`
- `created_at`, `updated_at`

### `users`
- `_id`, `email`, `hashed_password`, `organization_id`, `role` (always "admin")
- `status`: active | locked | inactive
- `password_history[]`, `password_expires_at`, `failed_login_attempts`
- `handoff_token`, `handoff_token_expires` (for central login flow)

### `organizations`
- `_id`, `name`, `slug` (unique, used in URLs)
- `workflows`: `{ workflow_name: { enabled, patient_schema, ... } }`
- `branding`: `{ logo, colors, ... }`
- `phone_number_id`, `staff_phone` (for call transfer)

### `sessions`
- `session_id` (UUID), `patient_id`, `organization_id`
- `status`: starting | running | completed | failed | terminated
- `phone_number`, `client_name`, `room_url`

### `audit_logs`
HIPAA-compliant audit trail (6-year TTL):
- `event_type`: login | logout | phi_access | api_access | password_reset
- `user_id`, `organization_id`, `ip_address`, `user_agent`, `timestamp`

## Authentication Flow

1. **Direct Login:** `POST /auth/login` → validates credentials → returns JWT
2. **Central Login:** Marketing site → `POST /auth/login-central` → handoff token → redirect to tenant → `POST /auth/exchange-token` → JWT

JWT payload: `{ sub: user_id, email, role, organization_id, organization_slug, exp }`

All authenticated endpoints use `Depends(get_current_user)` which:
- Validates JWT signature (HS256)
- Logs API access for audit
- Returns decoded payload as `current_user` dict

Organization-scoped access uses `Depends(require_organization_access)`.

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, routers, CORS, rate limiting |
| `dependencies.py` | Auth guards, DB injection, audit helpers |
| `database.py` | MongoDB connection (Motor async), `check_connection()` health check |
| `schemas.py` | Pydantic request/response models |
| `constants.py` | Status enums (SessionStatus, CallStatus, etc.) |
| `models/patient.py` | AsyncPatientRecord - patient data access (PHI) |
| `models/user.py` | AsyncUserRecord - user auth/management |
| `models/organization.py` | AsyncOrganizationRecord class |
| `sessions.py` | AsyncSessionRecord for call sessions |
| `audit.py` | AuditLogger for HIPAA compliance |
| `server_utils.py` | Daily room creation, bot start (local/production) |
| `api/health.py` | Root info and `/health` endpoint |
| `api/auth.py` | Login, logout, password reset, MFA endpoints |
| `api/patients.py` | Patient CRUD endpoints |
| `api/dialout.py` | Outbound call management |
| `api/dialin.py` | Inbound call webhook handler |
| `api/sessions.py` | Session list/detail/terminate |
| `api/metrics.py` | Call metrics and breakdowns |
| `api/webhooks.py` | Webhook CRUD and testing |
| `api/sms.py` | SMS inbound webhook and send |
| `api/admin.py` | Admin dashboard and call views |
| `api/onboarding.py` | Onboarding upload, transcription, conversations |

## Architecture Layers

```
API Layer (api/*.py)          → HTTP endpoints, auth, validation, audit
    ↓ calls
Data Access Layer (models/*.py) → Database operations (CRUD)
    ↓ uses
Database Layer (database.py)    → MongoDB connection, health check
```

- `models/patient.py` handles PHI - kept separate for HIPAA audit compliance
- `models/user.py` handles authentication - separate security domain
- Bot and handlers use models directly (no HTTP layer)

## Common Tasks

### Adding a new endpoint
1. Create/update router in `api/<module>.py`
2. Add Pydantic schemas in `schemas.py` if needed
3. Use `Depends(get_current_user)` for auth
4. Use `Depends(get_current_user_organization_id)` for org-scoped queries
5. Call `log_phi_access()` for any patient data access
6. Register router in `main.py` if new file

### Adding a patient field
Patient schema is dynamic (stored directly in MongoDB). No model changes needed.
1. Update frontend form and `patient_schema` in organization's workflow config
2. Field automatically flows through `PatientCreate` (uses `extra="allow"`)

### Adding organization config
1. Update org document in MongoDB with new field
2. Access in endpoints via `org_context["organization"].get("new_field")`

### Rate limiting
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_user_id_from_request)

@router.post("/endpoint")
@limiter.limit("10/minute")
async def my_endpoint(request: Request, ...):
```

### Environment variables
```bash
# Required
MONGO_URI, JWT_SECRET_KEY, ALLOWED_ORIGINS, DAILY_API_KEY
# Production only
PIPECAT_API_KEY, PIPECAT_AGENT_NAME
# Optional
ENV=local|production  # Controls bot start method
```
