Add navigation 

  Navigation Design Options

  Based on your requirements, here are two approaches:

  Option A: Top Navigation with Dropdown + Sub-navigation

  [Dashboard] [Workflows ▼] [Custom Reports]
                ├─ Prior Authorization ✓ → Click opens sub-nav
                ├─ Eligibility Verification (disabled)
                ├─ Visit Scheduling (disabled)
                └─ General Questions (disabled)

  When "Prior Authorization" clicked:
  [Dashboard] [Prior Authorization ▼] [Custom Reports]
                └─ (sub-menu appears below or as tabs)
                   [Dashboard] [Patient List] [Add Patient]

  Option B: Sidebar Navigation (More scalable)

  ┌─────────────────┬──────────────────────────┐
  │ Workflows       │                          │
  │  ├─ Prior Auth ▼│  Main Content Area       │
  │  │   Dashboard  │                          │
  │  │   Patients   │                          │
  │  │   Add Patient│                          │
  │  ├─ Eligibility │                          │
  │  ├─ Scheduling  │                          │
  │  └─ General Q   │                          │
  │                 │                          │
  │ Custom Reports  │                          │
  └─────────────────┴──────────────────────────┘

  ---
  My recommendation: Option B (Sidebar) because:
  1. Scales better as you add more workflows
  2. Clear visual hierarchy
  3. Easy to see what's enabled vs available
  4. Standard pattern users understand

  Questions:
  1. Which approach do you prefer (A or B)?
  2. Should the sidebar be collapsible?
  3. Should "Custom Reports" move into the sidebar or stay separate?


# Multi-Tenant Client Onboarding Implementation Plan

## Goal

Enable MyRobot to onboard new healthcare organizations as isolated tenants, each with:
- Their own users, patients, and call sessions (HIPAA-compliant data isolation)
- Custom patient data fields specific to their workflow needs
- Custom-branded dashboards accessible via subdomain
- Dedicated voice agent workflow per organization (1 org = 1 workflow in `clients/`)

All achieved with a **single codebase** for frontend, backend, and bot - no per-client forks or separate deployments.

---

## Architecture Decisions

### 1. Multi-Tenancy Model
**Decision**: Logical tenant isolation with `organization_id` on all records
**Why**: Simpler than separate databases, maintains single deployment, HIPAA-compliant with proper query filtering

### 2. URL Strategy
**Decision**: Subdomain routing (`clinic-a.datasova.com`)
**Why**: Clean separation, easy SSL with wildcard cert, professional appearance per client

### 3. Frontend Codebase
**Decision**: Single codebase with dynamic theming and org-specific patient forms
**Why**: One deployment to maintain, bug fixes apply universally, org config drives customization

### 4. Patient Data Fields
**Decision**: Common fields + `custom_fields` object per patient, schema defined in organization config
**Why**: Flexible per-org fields without schema changes, frontend renders forms dynamically based on org's patient_schema

### 5. Workflow-to-Organization Mapping
**Decision**: 1 org can have many workflows, but each workflow belongs to only 1 org
**Why**: Each workflow in `clients/` is custom-built for a specific organization's needs. Org has `enabled_flows` list (e.g., `["prior_auth", "benefits_check"]`). Backend validates `client_name` is in org's `enabled_flows` before starting call.

### 6. Docker Images
**Decision**: Single bot image containing all `clients/` workflows
**Why**: Shared warm capacity on Pipecat Cloud, simpler CI/CD, fast rebuilds (only Python files change)

---

## Database Schema Changes

### New: `organizations` Collection

```python
{
    "_id": ObjectId,
    "name": "Healthcare Clinic A",
    "slug": "clinic-a",                    # Used for subdomain routing
    "enabled_flows": ["prior_auth"],         # Workflows this org can use (each workflow belongs to only this org)
    "branding": {
        "company_name": "Clinic A"       # Displayed in dashboard header
    },
    "settings": {
        "default_phone": "+1234567890",
        "supervisor_phone": "+1987654321",
        "timezone": "America/New_York"
    },
    "patient_schema": {
        "fields": [
            {"key": "insurance_member_id", "label": "Member ID", "type": "string", "required": True},
            {"key": "cpt_code", "label": "CPT Code", "type": "string", "required": True},
            {"key": "provider_npi", "label": "Provider NPI", "type": "string", "required": True},
            {"key": "prior_auth_status", "label": "Status", "type": "select",
             "options": ["Pending", "Approved", "Denied"], "default": "Pending"}
        ]
    },
    "hipaa_baa_signed_at": datetime,       # Required before access
    "created_at": datetime,
    "updated_at": datetime
}
```

### Modified: `users` Collection

```python
{
    "_id": ObjectId,
    "email": "user@clinic-a.com",
    "password_hash": "...",
    "organization_id": ObjectId,           # NEW - required
    "role": "user",                        # user, admin, org_admin
    "status": "active",
    "created_at": datetime
}
```

### Modified: `patients` Collection

```python
{
    "_id": ObjectId,
    "organization_id": ObjectId,           # NEW - required for all queries
    "patient_name": "John Doe",
    "date_of_birth": "1990-01-15",
    "phone_number": "+1234567890",
    "custom_fields": {                     # NEW - org-specific data
        "insurance_member_id": "ABC123",
        "cpt_code": "99213",
        "provider_npi": "1234567890",
        "prior_auth_status": "Pending",
        "reference_number": None
    },
    "call_status": "Not Started",
    "call_transcript": {},
    "last_call_session_id": None,
    "created_at": datetime,
    "updated_at": datetime
}
```

### Modified: `sessions` Collection

```python
{
    "_id": ObjectId,
    "organization_id": ObjectId,           # NEW
    "patient_id": ObjectId,
    "client_name": "prior_auth",
    "status": "completed",
    "created_at": datetime
}
```

---

## Implementation Tasks

### Phase 1: Database & Models

#### 1.1 Create Organization Model
- [ ] Create `backend/models/organization.py` with `AsyncOrganizationRecord`
- [ ] Methods: `create()`, `get_by_id()`, `get_by_slug()`, `update()`, `list_all()`
- [ ] Index on `slug` (unique)

#### 1.2 Update User Model
- [ ] Add `organization_id` field to `AsyncUserRecord`
- [ ] Update `create_user()` to require `organization_id`
- [ ] Update `get_user_by_email()` to return `organization_id`
- [ ] Add method `get_users_by_organization()`

#### 1.3 Update Patient Model
- [ ] Add `organization_id` field (required, auto-populated from user's org)
- [ ] Add `custom_fields` dict field
- [ ] Update ALL query methods to filter by `organization_id`
- [ ] Remove hardcoded fields (insurance_member_id, cpt_code, etc.) - move to custom_fields
- [ ] Index on `organization_id`

#### 1.4 Update Session Model
- [ ] Add `organization_id` field
- [ ] Filter queries by `organization_id`

**Note**: No migration script needed - we will delete existing test data and rebuild with the new schema.

---

### Phase 2: Authentication & Authorization

#### 2.1 Update JWT Tokens
- [ ] Add `organization_id` to JWT payload in `backend/api/auth.py`
- [ ] Add `organization_slug` to token for frontend routing

#### 2.2 Create Tenant Context Dependency
- [ ] Create `backend/api/dependencies.py`
- [ ] `get_current_organization()` - extracts org from JWT
- [ ] `require_organization_access()` - validates user belongs to org
- [ ] Inject into all patient/session endpoints

#### 2.3 Update Auth Endpoints
- [ ] Modify `/register` to accept `organization_id` (admin creates orgs first)
- [ ] Modify `/login` response to include organization details (branding, client_name, patient_schema)

#### 2.4 Audit Logging
- [ ] Add `organization_id` to all PHI access logs
- [ ] Update `log_phi_access()` function

---

### Phase 3: API Endpoints

#### 3.1 Organization Management Endpoints
- [ ] `POST /organizations` - create new org (super admin only, used during onboarding)

#### 3.2 Update Patient Endpoints
- [ ] `GET /patients` - filter by org from JWT (user only sees their org's patients)
- [ ] `POST /patients` - auto-populate `organization_id` from JWT, validate custom_fields against schema
- [ ] `PUT /patients/{id}` - verify org ownership before update
- [ ] `DELETE /patients/{id}` - verify org ownership before delete

#### 3.3 Update Call Endpoints
- [ ] Pass `organization_id` to bot in session params
- [ ] Validate `client_name` from request is in org's `enabled_flows`

---

### Phase 4: Bot Integration

**How data flows to bot (already implemented):**
1. Backend fetches patient from DB → passes as `patient_data` in `SessionParams.data`
2. Bot receives via `args.body.get("patient_data")`
3. FlowLoader loads flow class based on `client_name`
4. Flow definition receives `patient_data` in constructor

**Changes needed:**

#### 4.1 Pass Organization Context
- [ ] Add `organization_id` to session params data (for tracing/logging)

#### 4.2 Update Flow Definitions for custom_fields
- [ ] Modify `PriorAuthFlow` to read from `patient_data["custom_fields"]` instead of top-level fields
- [ ] Example: `patient_data["custom_fields"]["cpt_code"]` instead of `patient_data["cpt_code"]`
- [ ] Update function handlers (e.g., `update_prior_auth_status`) to write to `custom_fields`

#### 4.3 Tracing (Optional Enhancement)
- [ ] Add `organization.id` to OpenTelemetry span attributes for filtering in Langfuse

---

### Phase 5: Frontend Changes

#### 5.1 Subdomain Detection & Org Context
- [ ] Create `src/utils/tenant.ts` - extract org slug from subdomain
- [ ] Store org context in React context after login
- [ ] Org config comes from login response (branding, patient_schema, client_name)

#### 5.2 Auth Flow Updates
- [ ] Store organization details in auth state after login
- [ ] Login response includes full org config

#### 5.3 Update Patient Form for custom_fields
- [ ] Modify existing `PatientForm.tsx` to render fields from `organization.patient_schema`
- [ ] Replace hardcoded fields (insurance_member_id, cpt_code, etc.) with dynamic rendering
- [ ] Handle field types: string, select, date
- [ ] Validate required fields based on schema
- [ ] Remove old hardcoded field components (don't create new file, update existing)

#### 5.4 Apply Organization Branding
- [ ] Display `branding.company_name` in dashboard header
- [ ] Update patient list columns based on schema fields

#### 5.5 Type Updates
- [ ] Add `Organization` type to `types.ts`
- [ ] Update `Patient` type with `custom_fields: Record<string, any>`
- [ ] Update `AuthResponse` to include organization

#### 5.6 API Client Updates
- [ ] Update `api.ts` to handle patient with custom_fields
- [ ] Remove hardcoded patient field types

---

### Phase 6: Deployment & Infrastructure

#### 6.1 Vercel Configuration
- [ ] Configure wildcard subdomain (`*.datasova.com`)
- [ ] Update `vercel.json` for subdomain routing
- [ ] Environment variable for base domain

#### 6.2 Database Indexes
- [ ] Add compound indexes: `(organization_id, created_at)` on patients
- [ ] Add unique index on `organizations.slug`
- [ ] Add index on `users.organization_id`

#### 6.3 Environment Variables
- [ ] Add `BASE_DOMAIN=datasova.com` to frontend
- [ ] Document new env vars

---

### Phase 7: Testing & Validation

**Focus on reliability over test coverage - keep it simple and HIPAA compliant.**

#### 7.1 Manual Testing Checklist
- [ ] User from Org A cannot see Org B's patients (critical HIPAA requirement)
- [ ] Patient creation auto-populates organization_id from JWT
- [ ] Login returns correct org config with patient_schema
- [ ] Call startup works end-to-end with organization context
- [ ] Bot receives patient data with custom_fields

#### 7.2 Verify Data Isolation
- [ ] All patient queries include organization_id filter
- [ ] Session queries include organization_id filter
- [ ] No API endpoint returns cross-org data

#### 7.3 Documentation
- [ ] Update CLAUDE.md with multi-tenancy architecture
- [ ] Document new organization onboarding steps

---

## Adding a New Organization

1. **Create workflow(s)** in `clients/` for this org:
   - `clients/<workflow_name>/flow_definition.py`
   - `clients/<workflow_name>/services.yaml`

2. **Create organization record** in MongoDB:
   ```python
   {
       "name": "New Clinic",
       "slug": "new-clinic",
       "enabled_flows": ["<workflow_name>"],
       "patient_schema": { "fields": [...] },
       "branding": { "company_name": "New Clinic" }
   }
   ```

3. **Create admin user** with `organization_id`

4. **Deploy**: `./deploy-test.sh` then `./deploy-prod.sh`

5. **Configure DNS** for subdomain (if not using wildcard)

6. Organization accesses via `{slug}.datasova.com`

No frontend or backend code changes required - just workflow code and database records.

---

## HIPAA Compliance Checklist

- [ ] All queries filter by `organization_id` (no cross-tenant access)
- [ ] JWT tokens include `organization_id` for stateless validation
- [ ] Audit logs include `organization_id`
- [ ] BAA tracking per organization (`hipaa_baa_signed_at`)
- [ ] PHI access logged with org context
- [ ] Database indexes support efficient org-scoped queries
- [ ] No patient data in URLs or logs without org context

---

## File Changes Summary

### New Files
- `backend/models/organization.py`
- `backend/api/dependencies.py`
- `backend/api/organizations.py`
- `scripts/migrate_to_multitenancy.py`
- `frontend/src/utils/tenant.ts`
- `frontend/src/components/DynamicPatientForm.tsx`
- `frontend/src/context/OrganizationContext.tsx`

### Modified Files
- `backend/models.py` - Add organization_id to User, Patient, Session
- `backend/api/auth.py` - JWT with org context, org config endpoint
- `backend/api/patients.py` - Org-scoped queries
- `backend/api/calls.py` - Pass org to bot
- `bot.py` - Receive org context
- `clients/prior_auth/flow_definition.py` - Use custom_fields
- `frontend/src/App.tsx` - Subdomain detection, org context
- `frontend/src/types.ts` - Organization type, updated Patient
- `frontend/src/api.ts` - Org-aware API calls
- `frontend/src/components/PatientForm.tsx` - Dynamic fields
- `frontend/vercel.json` - Subdomain routing