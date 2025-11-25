# Pre-Production Audit: 2025-11-25

## Architecture Overview

Healthcare voice AI system for prior authorization verification. FastAPI backend on Fly.io connects React frontend (Vercel) and Pipecat voice bot (Pipecat Cloud) to MongoDB, with multi-tenant organization isolation via JWT tokens containing `organization_id`.

## Component Map

```
[Frontend - React/Vercel]
       |
       | JWT Bearer Token
       v
[Backend - FastAPI/Fly.io]
       |
       | organization_id filter
       v
[MongoDB Atlas]
       ^
       |
[Bot - Pipecat Cloud] <-- patient_data + organization_id via API
```

Collections: `patients`, `users`, `organizations`, `sessions`, `audit_logs`

---

## Audit Buckets

### Bucket 1: PHI Data Protection
**Risk Level**: Critical
**Components Affected**: MongoDB, Backend, Bot

**Issues Found**:
- [ ] PHI stored in plaintext in MongoDB (patient names, DOB, SSN-adjacent member IDs, transcripts)
- [ ] Call transcripts contain full conversation audio transcriptions without field-level encryption
- [ ] Daily.co recordings created during calls (deleted after, but window of exposure exists)

**Recommended Fixes**:
1. Enable MongoDB field-level encryption for PHI fields (`patient_name`, `date_of_birth`, `insurance_member_id`, `call_transcript`)
2. Configure MongoDB Atlas encryption at rest with customer-managed keys (CMK) for HIPAA compliance

---

### Bucket 2: Authentication & Session Security
**Risk Level**: Critical
**Components Affected**: Backend `/auth/*`, Frontend

**Issues Found**:
- [ ] Password reset token returned directly in API response (`auth.py:306-309`) instead of emailed - allows token interception
- [ ] JWT secret key has no minimum length validation at startup (only env var presence checked)
- [ ] Frontend stores JWT in localStorage (`auth.ts:16`) - vulnerable to XSS attacks
- [ ] No refresh token mechanism - 30-minute session timeout with hard logout

**Recommended Fixes**:
1. Implement email-based password reset flow - never return reset tokens in HTTP responses
2. Add `len(SECRET_KEY) >= 32` validation in `config.py` startup checks
3. Consider httpOnly cookies for JWT storage instead of localStorage
4. Implement refresh token rotation for longer sessions without full re-auth

---

### Bucket 3: Multi-Tenant Isolation
**Risk Level**: High
**Components Affected**: Backend, Bot, Database

**Issues Found**:
- [x] ~~Bot's `save_call_transcript()` (`handlers/transcript.py:125`) doesn't pass `organization_id` to DB write~~ **FIXED**
- [ ] `find_patient_by_id()` `organization_id` parameter is optional - callers can bypass tenant filter
- [ ] No database-level row security - tenant isolation relies entirely on application code
- [ ] Organization slug extracted from subdomain in frontend (`tenant.ts`) without backend validation

**Recommended Fixes**:
1. Make `organization_id` required parameter in all patient DB operations - remove optional flag
2. ~~Add `organization_id` to `save_call_transcript()` call in `handlers/transcript.py`~~ **DONE**
3. Add backend validation that user's JWT org matches requested subdomain on all endpoints
4. Consider MongoDB views or stored procedures that enforce tenant scoping

---

### Bucket 4: Authorization & RBAC
**Risk Level**: High
**Components Affected**: Backend API endpoints

**Issues Found**:
- [ ] JWT contains `role` field but no middleware enforces role-based permissions
- [ ] All authenticated users can perform all operations (CRUD patients, start calls, view transcripts)
- [ ] No admin/viewer distinction - everyone has full access within their org
- [ ] User management endpoints missing (no way to list/deactivate users in API routes)

**Recommended Fixes**:
1. Create `require_role()` FastAPI dependency that validates JWT role against endpoint requirements
2. Define permission matrix: `admin` (all ops), `user` (view/call), `viewer` (read-only)
3. Add `/users` API routes for user management with admin-only access

---

### Bucket 5: Bot Security Boundaries
**Risk Level**: Medium
**Components Affected**: Bot, Pipecat Cloud

**Issues Found**:
- [ ] Bot receives full `patient_data` dict including all PHI fields - no data minimization
- [ ] Bot database writes (`flow_definition.py`) don't use audit logging for PHI access
- [ ] Local bot server (`bot.py:170-227`) has no authentication on `/start` endpoint
- [ ] Supervisor phone number stored in plaintext in patient data, exposed to bot

**Recommended Fixes**:
1. Create filtered `patient_data_for_bot()` helper that passes only required fields for verification
2. Add audit logging for bot database operations (status updates, reference number saves)
3. Add shared secret authentication to local bot `/start` endpoint for dev security
4. Move supervisor phone to organization config rather than patient-level data

---

### Bucket 6: Audit Logging Gaps
**Risk Level**: Medium
**Components Affected**: Backend, Bot, Audit system

**Issues Found**:
- [ ] Bot PHI access (reading patient data, updating status) not logged to `audit_logs` collection
- [ ] Audit logs have TTL of 6 years but no backup/export mechanism for HIPAA retention requirements
- [ ] Failed authorization attempts (wrong org) logged but not alertable
- [ ] No audit log for admin operations (user creation, password changes by admin)

**Recommended Fixes**:
1. Create `audit_logger_for_bot()` that bot can call after each DB read/write operation
2. Implement audit log export to S3/GCS with separate retention for HIPAA 6-year minimum
3. Add alerting webhook for >3 failed login attempts or cross-org access attempts
4. Add admin action logging to `create_user()`, `lock_account()`, `unlock_account()` functions

---

### Bucket 7: API Security Hardening
**Risk Level**: Medium
**Components Affected**: Backend API

**Issues Found**:
- [ ] Rate limiting uses IP fallback when JWT unavailable - can be bypassed with rotating IPs
- [ ] No request body size limits configured for bulk patient upload
- [ ] Error responses include full stack traces in some cases (`traceback.format_exc()` in logs only, but detail=str(e))
- [ ] CORS `ALLOWED_ORIGINS` configuration not validated for production domains only

**Recommended Fixes**:
1. Require authentication for all patient endpoints - no IP-only rate limiting fallback
2. Add `max_length` validators to Pydantic models and configure FastAPI body size limits
3. Replace `detail=str(e)` with generic error messages; use correlation IDs for debugging
4. Validate `ALLOWED_ORIGINS` contains only production domains at startup

---

## Priority Action Items

1. Implement MongoDB field-level encryption for PHI (patient_name, DOB, member_id, transcripts)
2. Fix password reset to use email delivery instead of returning token in response
3. Make `organization_id` required (not optional) in all database query functions
4. Add audit logging for bot PHI access operations
5. Create role-based authorization middleware enforcing user/admin permissions

## Pre-Deployment Checklist

- [ ] Enable MongoDB Atlas encryption at rest with customer-managed keys
- [ ] Implement field-level encryption for PHI fields in patient documents
- [ ] Replace password reset token API response with email delivery
- [ ] Add `JWT_SECRET_KEY` minimum length (32+ chars) validation at startup
- [ ] Make `organization_id` required parameter in `find_patient_by_id()`, `update_patient()`, `delete_patient()`
- [x] ~~Add `organization_id` to bot's `save_call_transcript()` database write~~ **FIXED**
- [ ] Create `require_role()` dependency and apply to admin-only endpoints
- [ ] Add audit logging for bot database operations
- [ ] Configure CORS `ALLOWED_ORIGINS` validation for production domains only
- [ ] Review and test all API endpoints for proper tenant isolation under load
