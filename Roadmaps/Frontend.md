### Frontend Roadmap

Core functionality: adding patients via a form (aligned with the database schema from `models.py`, including fields like patient_name, date_of_birth, insurance_member_id, cpt_code, provider_npi, prior_auth_status, etc.), listing patients with statuses, patient detail views for starting calls and tracking progress (real-time status & post-call full transcripts). This focuses on minimal viable features for iteration.

Key insights from analysis:
- The current frontend is static HTML/JS with basic LiveKit integration, which works for prototyping but struggles with dynamic states (e.g., real-time transcripts, multi-call monitoring). Migrating to a framework is essential for reactivity.
- Multi-call support is a future need, but for prototype, start with single-call focus and design for easy extension (e.g., a list of sessions).
- Real phone calls to PSTN numbers require bridging WebRTC (LiveKit) with telephony; the backend currently uses LiveKit for WebRTC rooms (bot-user simulation), so integrate Twilio for outbound/inbound PSTN calls while keeping LiveKit for media handling.
- Auth is important for sensitive data but not strictly core; propose a simple implementation but mark it as optional/deferrable.
- Deployment on fly.io via GitHub is fine; no need to change for now.
- Avoid over-engineering: Use REST for backend integration (e.g., /patients, /start-call), polling or WebSockets for real-time (transcripts/statuses). Skip advanced features until prototype works.

This plan targets a functional prototype in 4-6 steps, testable end-to-end.

### Resolved Decisions

- **UI Framework**: Use React (via Create React App). It's ideal for reactive UIs, handling lists (e.g., patients, calls), and real-time updates (e.g., via WebSockets or polling). It scales well for multi-call monitoring (e.g., dashboard with tabs/lists) without complexity. Vue.js is lighter but React has better ecosystem support for WebRTC/LiveKit integrations and future multi-agent views.
- **Authentication**: Use JWT with a simple login form tied to FastAPI backend (add a /login endpoint returning JWT). Store token in localStorage for API calls. This is quick (1-2 hours) and secures patient data without third-party services like Auth0. Defer if prototype is internal/testing-only.
- **Multi-Session/Call Handling**: Use React for a dashboard-like list of active sessions. For real phone calls: Keep LiveKit for media/transcripts, but integrate Twilio (via backend) for PSTN (outbound to insurance numbers, inbound from patients). Update backend (e.g., pipeline.py) to use Twilio SDK for call initiation, routing media to LiveKit rooms. Frontend polls /active-sessions or uses WebSockets (e.g., via Socket.io) for statuses/transcripts. This enables monitoring multiple calls in a list.
- **Deployment**: Stick with fly.io and GitHub. It's already set up; add frontend build to Dockerfile for unified deployment.
- **Other Tech**: Use Axios for API calls, Tailwind CSS for quick styling (avoids raw CSS debt). For real-time: Start with polling (simple), upgrade to WebSockets later.

### Step-by-Step Implementation Plan

Focus on sequential steps, starting from the current static HTML. Each step includes estimated effort (low/medium) and backend assumptions (e.g., existing REST endpoints like /patients, /start-call from app.py). Test incrementally (e.g., after Step 2, verify patient list loads). 
## ALWAYS implement the minimal changes that are needed to achieve the goal desired. 
## ALWAYS test after every change to ensure reliability. 

1. **Set Up React App and Basic Structure (Medium Effort, 2-4 hours)**  
   Migrate the static HTML/JS to React. This stabilizes the base and enables reactive components.  
   - Install Create React App: `npx create-react-app frontend --template typescript` (use TS for type safety with patient schema).  
   - Move existing HTML/JS into components (e.g., App.tsx with tabs for List/Add/Detail).  
   - Integrate LiveKit SDK: `npm install @livekit/components-react` 
   - Add Axios: `npm install axios`. 
   - Optional: Add Tailwind: Follow setup guide for quick styling 
   - Structure: Create routes/tabs for "Patients List", "Add Patient", "Patient Details" (use react-router-dom for navigation).  
   - Backend Integration: None yet; focus on UI skeleton.  
   - Test: Run locally (`npm start`), ensure basic tabs work.

2. **Implement Patient List View (Low Effort, 1-2 hours)**  
   Fetch and display patients from backend. This achieves the list goal.  
   - Create a `PatientList` component: Use useEffect to fetch from `/patients` (assume it returns array of patients with fields like patient_id, patient_name, prior_auth_status).  
   - Start a call for a patient by clicking "Start Call" next to patient name on the list
   - Display in a table/list: Columns for name, status, insurance_company, etc. Make rows clickable to navigate to details (pass patient_id via URL or state).  
   - Handle loading state simply (spinner).  
   - Add auth if not deferring: Wrap in auth check; fetch token from /login on app load.  
   - Backend: Use existing /patients endpoint.  
   - Test: Load real data; click navigates to a stub detail view.

3. **Implement Add Patient Form (Medium Effort, 2-3 hours)**  
   Create a form mirroring the schema (from models.py: patient_name, date_of_birth, insurance_member_id, cpt_code, provider_npi, insurance_company_name, facility_name, appointment_time, prior_auth_status="Pending", etc.).  
   - Create `AddPatientForm` component: Use controlled inputs (useState). Validate basics (e.g., required fields).  
   - On submit: POST to new backend endpoint `/add-patient` (add this to app.py: insert via patient_db). Redirect to list on success.  
   - UI: Simple form in a tab; include date picker for DOB/appointment_time.  
   - Backend: Add /add-patient route in app.py (Pydantic model for validation).  
   - Test: Add a patient, verify it appears in list.

4. **Implement Patient Detail View (Medium Effort, 3-4 hours)**  
   Display patient details, call details, transcripts once available.  
   - Create `PatientDetail` component: Fetch full patient via `/patients/{patient_id}` (add endpoint if needed, using get_complete_patient_record). Show all fields.  
   - Integrate LiveKit: Use <LiveKitRoom> component to join room (with user_token). Show mic controls (mute/unmute).  - DECISION: No, at this implementation, I should not be able to join the call from the UI and talk to the agent. I can only start the call and then the agent calls the insurance company through twillio.   
   - Transcripts: Once the call is completed, I want to see the full transcript on the Patient Detail tab. 

5. **Add Basic Multi-Session Support (Low Effort, 1-2 hours)**  
   Enable monitoring multiple calls (deferred but foundational).  
   - In the existing Patient List view, show the call statuses - not important for now
   - Test: Start multiple calls from the list view, monitor in list.

6. **Polish and Deploy (Low Effort, 1 hour)**  
   - Build React app: `npm run build`.  
   - Update Dockerfile to include frontend build (copy build/ to /app/static, serve via FastAPI).  
   - Deploy to fly.io via GitHub (push updates).  
   - Test End-to-End: Add patient → List → Detail → Start call → See transcripts/status.

### Recommended Code/Structures

**File Structure** (in /frontend):

frontend/
├── src/
│   ├── components/
│   │   ├── PatientList.tsx  // Table with clickable rows
│   │   ├── AddPatientForm.tsx  // Form with inputs matching schema
│   │   ├── PatientDetail.tsx  // Details + call controls + transcript viewer
│   │   └── ActiveSessions.tsx  // List of sessions
│   ├── App.tsx  // Main app with router/tabs (use react-router-dom)
│   ├── api.ts  // Axios wrappers for endpoints (e.g., getPatients(), startCall(patient_id))
│   └── types.ts  // Patient interface mirroring schema (e.g., { patient_id: string; patient_name: string; ... })
├── public/  // Static assets
└── package.json
