# Multi-Tenant Implementation Plan

## End Goal (North Star)

**Two demo clients running simultaneously from the same codebase:**

### DemoClinicAlpha
- **URL**: `democlinicalpha.datasova.com`
- **Workflow directories**:
  - `clients/demo_clinic_alpha/prior_auth/`
  - `clients/demo_clinic_alpha/patient_questions/`
- **Purpose**:
  - `prior_auth`: Outbound calls to insurance companies for prior auth verification
  - `patient_questions`: Outbound calls for patient check-ins
- **Workflows**: 2 (prior_auth, patient_questions)

### DemoClinicBeta
- **URL**: `democlinicbeta.datasova.com`
- **Workflow directory**: `clients/demo_clinic_beta/patient_questions/`
- **Purpose**: Outbound calls for patient check-ins
- **Workflows**: 1 (patient_questions)

### Success Criteria
- Both frontends accessible at their subdomains
- Each has its own users, patients, and organization config in MongoDB
- Users from Alpha cannot see Beta's patients (HIPAA isolation)
- Starting a call from Alpha prior_auth runs `clients/demo_clinic_alpha/prior_auth/`
- Starting a call from Alpha patient_questions runs `clients/demo_clinic_alpha/patient_questions/`
- Starting a call from Beta runs `clients/demo_clinic_beta/patient_questions/`
- Can test all three simultaneously in 3 separate browser tabs

### Directory Structure
```
clients/
├── demo_clinic_alpha/
│   ├── prior_auth/
│   │   ├── flow_definition.py
│   │   └── services.yaml
│   └── patient_questions/
│       ├── flow_definition.py
│       └── services.yaml
└── demo_clinic_beta/
    └── patient_questions/
        ├── flow_definition.py
        └── services.yaml
```

---

## Data Model Vision

### Core Principles
1. **Database is single source of truth** - Organization document defines all workflows and their schemas
2. **Workflow-level patient schemas** - Each workflow has its own `patient_schema` with distinct fields
3. **Flat patient storage** - Patient fields stored flat in `patients` collection with both `organization_id` AND `workflow` fields
4. **Workflow-scoped UI** - User logs in, selects a workflow, then sees Patient List/Dashboard/Add Patient scoped to that org+workflow only
5. **Zero latency bot execution** - All patient fields pushed to bot context on call-start; no database trips during the phone call

### Data Flow
1. **Login** → Backend returns org with all workflows and their schemas
2. **Workflow selection** → User picks workflow (e.g., "prior_auth"), frontend scopes all views to that workflow
3. **Patient list** → Shows only patients for selected org+workflow
4. **Add patient** → Form renders from selected workflow's `patient_schema`
5. **Start call** → Backend fetches patient, passes all fields flat to bot
6. **Bot execution** → Bot loads `clients/{org_slug}/{workflow}/`, all patient data already in memory

### Key Relationships
- **Organization** → has many **Workflows** (each with `patient_schema`)
- **Workflow** → has many **Patients** (via `patient.organization_id` + `patient.workflow`)
- **Patient** → has many **Sessions** (via `session.patient_id`)
- **Session** → stores **Transcript**

### No Hardcoding
- No hardcoded fields in backend schemas
- No hardcoded fields in frontend forms
- No hardcoded fields in bot flows
- Everything derives from `org.workflows[workflow_name].patient_schema.fields`

---

## Database Schema

### Organizations Collection
```javascript
{
  _id: ObjectId,
  name: "Demo Clinic Alpha",
  slug: "demo_clinic_alpha",
  branding: { company_name: "Demo Clinic Alpha" },
  workflows: {
    "prior_auth": {
      enabled: true,
      patient_schema: {
        fields: [
          { key: "patient_name", label: "Patient Name", type: "string", required: true, display_in_list: true, display_order: 1 },
          { key: "date_of_birth", label: "Date of Birth", type: "date", required: true, display_in_list: true, display_order: 2 },
          { key: "insurance_member_id", label: "Member ID", type: "string", required: true, display_in_list: true, display_order: 3 },
          { key: "insurance_company_name", label: "Insurance Company", type: "string", required: true, display_in_list: false, display_order: 4 },
          { key: "insurance_phone", label: "Insurance Phone", type: "string", required: true, display_in_list: true, display_order: 5 },
          { key: "facility_name", label: "Facility", type: "string", required: true, display_in_list: false, display_order: 6 },
          { key: "provider_name", label: "Provider Name", type: "string", required: true, display_in_list: false, display_order: 7 },
          { key: "provider_npi", label: "Provider NPI", type: "string", required: true, display_in_list: false, display_order: 8 },
          { key: "cpt_code", label: "CPT Code", type: "string", required: true, display_in_list: true, display_order: 9 },
          { key: "appointment_time", label: "Appointment Time", type: "datetime", required: true, display_in_list: false, display_order: 10 },
          { key: "supervisor_phone", label: "Supervisor Phone", type: "string", required: false, display_in_list: false, display_order: 11 },
          { key: "prior_auth_status", label: "Auth Status", type: "select", options: ["Pending", "Approved", "Denied"], default: "Pending", required: false, display_in_list: true, display_order: 12 },
          { key: "reference_number", label: "Reference #", type: "string", required: false, display_in_list: true, display_order: 13 }
        ]
      }
    }
  },
  created_at: ISODate,
  updated_at: ISODate
}
```

### Users Collection
```javascript
{
  _id: ObjectId,
  email: "adambehun22@gmail.com",
  hashed_password: "...",
  organization_id: ObjectId("alpha_org_id"),
  role: "admin",
  status: "active"
}
```

### Patients Collection
```javascript
{
  _id: ObjectId,
  organization_id: ObjectId("alpha_org_id"),
  workflow: "prior_auth",
  // All fields stored flat - keys match workflow's patient_schema.fields[].key
  patient_name: "John Smith",
  date_of_birth: "03/15/1985",
  insurance_member_id: "ABC123456",
  insurance_company_name: "Aetna",
  insurance_phone: "+15165667132",
  facility_name: "City Medical Center",
  provider_name: "Dr. Jane Wilson",
  provider_npi: "1234567890",
  cpt_code: "99213",
  appointment_time: "12/01/2024 02:30 PM",
  supervisor_phone: "+15165551234",
  prior_auth_status: "Pending",
  reference_number: null,
  // System fields
  call_status: "Not Started",
  created_at: ISODate,
  updated_at: ISODate
}
```

### Sessions Collection
```javascript
{
  _id: ObjectId,
  session_id: "uuid-string",
  organization_id: ObjectId("alpha_org_id"),
  patient_id: ObjectId("patient_id"),
  workflow: "prior_auth",
  status: "completed",  // "in_progress", "completed", "failed"
  // Call metadata
  phone_number: "+15165667132",
  started_at: ISODate,
  ended_at: ISODate,
  duration_seconds: 245,
  // Transcript
  transcript: {
    messages: [
      { role: "assistant", content: "Hi, this is Alexandra...", timestamp: ISODate },
      { role: "user", content: "Hello, this is Jennifer from Aetna...", timestamp: ISODate }
    ],
    summary: "Authorization approved, reference number AUTH123456"
  },
  created_at: ISODate,
  updated_at: ISODate
}
```

### Audit Logs Collection (HIPAA Compliance)
```javascript
{
  _id: ObjectId,
  event_type: "phi_access",  // "login", "logout", "phi_access", "api_access"
  user_id: "user_id_string",
  organization_id: "org_id_string",
  // For PHI access events
  action: "view",  // "create", "update", "delete", "export"
  resource_type: "patient",  // "transcript", "call"
  resource_id: "patient_id_string",
  endpoint: "/patients/123",
  // For auth events
  email: "user@example.com",
  success: true,
  // Common fields
  ip_address: "192.168.1.1",
  user_agent: "Mozilla/5.0...",
  timestamp: ISODate,
  details: {}  // Additional context
}
// Note: 6-year TTL index for HIPAA retention compliance
```

---

## Collections Summary
- **organizations** - Org config, workflows, patient schemas
- **users** - User accounts linked to organizations
- **patients** - Patient data (flat fields per workflow schema)
- **sessions** - Call sessions with transcripts
- **audit_logs** - HIPAA-compliant audit trail (6-year retention)

---

## Implementation Principles
1. **Database is single source of truth** - All field definitions come from `org.workflows[workflow].patient_schema`
2. **Minimal changes only** - Don't refactor unrelated code. Don't add features not specified.
3. **Reuse existing patterns** - Follow the codebase's existing style.
4. **Zero latency impact** - Schema fetched once at login, patient data pushed to bot on call-start.
5. **HIPAA compliance is non-negotiable** - Every query MUST filter by `organization_id`.
6. **Tenant isolation** - Users only see their org's data, scoped by workflow.

---

## Phase 6: Local Testing & Deployment

### 6.1 Create Demo Organizations in MongoDB
```bash
python scripts/setup_multi_tenant.py
```

### 6.2 Local End-to-End Testing

1. **Start backend** (Terminal 1): `ENV=local python app.py`
2. **Start bot server** (Terminal 2): `python bot.py`
3. **Start frontend for Alpha** (Terminal 3): `cd frontend && PORT=3000 npm run dev`
4. **Start frontend for Beta** (Terminal 4): `cd frontend && PORT=3001 npm run dev`

5. **Test DemoClinicAlpha workflow:**
   - Open `http://localhost:3000`
   - Login: `adambehun22@gmail.com` / `REDACTED`
   - Select `prior_auth` workflow
   - Create patient with prior auth fields
   - Start call → verify bot loads `clients/demo_clinic_alpha/prior_auth/`

6. **Test DemoClinicBeta workflow:**
   - Open `http://localhost:3001`
   - Login: `adam@datasova.com` / `REDACTED`
   - Select `patient_questions` workflow
   - Create patient with patient questions fields
   - Start call → verify bot loads `clients/demo_clinic_beta/patient_questions/`

7. **Verify tenant isolation:**
   - Alpha user cannot see Beta's patients
   - Beta user cannot see Alpha's patients

### 6.3 Configure DNS & Vercel for Subdomains
- Configure wildcard subdomain `*.datasova.com` OR specific subdomains
- Set up `democlinicalpha.datasova.com` → Vercel
- Set up `democlinicbeta.datasova.com` → Vercel

### 6.4 Deploy to Production
1. **Deploy backend to Fly.io:** `fly deploy`
2. **Deploy bot to Pipecat Cloud:**
   ```bash
   docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .
   pipecatcloud deploy
   ```
3. **Deploy frontend to Vercel:** `cd frontend && vercel --prod`
4. **Verify production:** Test login at both subdomains, test full call flow for both organizations