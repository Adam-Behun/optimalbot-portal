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

### Calls
| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/start-call` | Initiate dial-out call | Yes |
| GET | `/call/{session_id}/status` | Get call status | Yes |
| GET | `/call/{session_id}/transcript` | Get call transcript | Yes |
| DELETE | `/call/{session_id}` | End call session | Yes |
| POST | `/dialin-webhook/{org}/{workflow}` | Daily.co incoming call webhook | No* |

*Dial-in webhook is unauthenticated but validates org slug exists.

## MongoDB Collections

### `patients`
Dynamic schema per workflow. Common fields:
- `_id`, `organization_id`, `workflow`
- `call_status`: Not Started | Dialing | In Progress | Completed | Failed | Supervisor Dialed | Voicemail
- `call_transcript`, `last_call_session_id`, `last_call_timestamp`
- `created_at`, `updated_at`

### `users`
- `_id`, `email`, `hashed_password`, `organization_id`, `role` (user|admin)
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
| `database.py` | MongoDB connection (Motor async) |
| `schemas.py` | Pydantic request/response models |
| `constants.py` | Status enums (SessionStatus, CallStatus, etc.) |
| `models/patient_user.py` | AsyncPatientRecord, AsyncUserRecord classes |
| `models/organization.py` | AsyncOrganizationRecord class |
| `sessions.py` | AsyncSessionRecord for call sessions |
| `audit.py` | AuditLogger for HIPAA compliance |
| `server_utils.py` | Daily room creation, bot start (local/production) |
| `api/auth.py` | Login, logout, password reset endpoints |
| `api/patients.py` | Patient CRUD endpoints |
| `api/dialout.py` | Outbound call management |
| `api/dialin.py` | Inbound call webhook handler |

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
