# Dial-In Implementation Plan

## Goals

- Enable dial-in functionality alongside existing dial-out in the MyRobot codebase
- Support multiple clients with different workflows where each workflow can be either dial-in or dial-out
- Make `patient_questions` workflow as **dial-in** (users call in)
- Keep `prior_auth` workflow as **dial-out** (bot calls out)
- Maintain modular, minimal code changes, no redundancy and no bakward compatibility
- Both dial-in and dial-out should run simultaneously for the same client

## Reference Examples (CRITICAL)

**You MUST study these example directories before implementing any phase:**

- **Dial-In Example:** `/home/nope-4-0/projects/MyRobot/dialy-pstn-dial-in-example/`
  - `bot.py` - Shows DailyDialinSettings usage, on_first_participant_joined handler
  - `server.py` - Shows webhook endpoint that receives Daily PSTN calls
  - `server_utils.py` - Shows room creation with sip_caller_phone, AgentRequest model

- **Dial-Out Example:** `/home/nope-4-0/projects/MyRobot/daily-pstn-dial-out-example/`
  - `bot.py` - Shows dial-out transport configuration
  - `server.py` - Shows how dial-out is initiated
  - `server_utils.py` - Shows room creation for dial-out

**Follow these examples religiously. Do not deviate from their patterns.**

## Key Differences: Dial-Out vs Dial-In

| Aspect | Dial-Out | Dial-In |
|--------|----------|---------|
| Initiator | Bot calls user | User calls bot |
| Daily Room | `enable_dialout: true` | `sip_mode: "dial-in"` |
| Transport Config | `phone_number_id` | `dialin_settings` (call_id, call_domain) |
| Entry Event | `on_joined` → `start_dialout()` | `on_first_participant_joined` |
| Bot Behavior | Waits for user to speak | Bot speaks first |
| Webhook | Not needed | Required (Daily sends incoming call data) |

---

## Phase 1: Configuration & Data Model

### Phase 1 Actual Implementation

**Files Modified:**
1. `clients/demo_clinic_alpha/prior_auth/services.yaml` - Added `call_type: "dial-out"`
2. `clients/demo_clinic_alpha/patient_questions/services.yaml` - Added `call_type: "dial-in"`
3. `clients/demo_clinic_beta/patient_questions/services.yaml` - Added `call_type: "dial-in"`
4. `pipeline/pipeline_factory.py` - Extract call_type from services config, pass to components
5. `pipeline/runner.py` - Added call_type parameter to ConversationPipeline.__init__
6. `bot.py` - Extract call_type from request body, pass to ConversationPipeline

**Key Changes:**
- `call_type` is a required field in services.yaml (no backwards compatibility)
- `PipelineFactory.build()` extracts call_type and raises ValueError if missing
- `call_type` is passed through components dict from factory to runner
- `ConversationPipeline` stores call_type as instance variable for use by handlers
- bot.py validates call_type is present in request body

**Issues Encountered:**
- None

---

## Phase 2: Transport & Handler Configuration

### Phase 2 Actual Implementation

**Files Modified:**

1. `services/service_factory.py`
   - Added `DailyDialinSettings` import
   - Modified `create_transport()` to accept optional `dialin_settings` parameter
   - Conditionally creates transport with `dialin_settings` (dial-in) or `phone_number_id` (dial-out)

2. `handlers/transport.py`
   - Added `setup_transport_handlers(pipeline, call_type)` - routes to appropriate handler
   - Added `setup_dialin_handlers(pipeline)` with:
     - `on_first_participant_joined`: Initializes flow with greeting node
     - `on_client_disconnected`: Updates status, saves transcript, cancels task
     - `on_dialin_error`: Updates status to Failed, saves transcript, cancels task

3. `handlers/__init__.py`
   - Exported `setup_dialin_handlers` and `setup_transport_handlers`

4. `pipeline/pipeline_factory.py`
   - Modified `build()` to accept optional `dialin_settings` parameter
   - Passes `dialin_settings` to `ServiceFactory.create_transport()`

5. `pipeline/runner.py`
   - Added `dialin_settings` parameter to `ConversationPipeline.__init__`
   - Passes `dialin_settings` to `PipelineFactory.build()`
   - Changed `setup_dialout_handlers(self)` to `setup_transport_handlers(self, self.call_type)`
   - IVR handlers only setup for dial-out calls

6. `clients/demo_clinic_alpha/patient_questions/flow_definition.py`
   - Removed all IVR-related methods (`create_greeting_node_after_ivr_completed`, `create_greeting_node_without_ivr`)
   - Added single `create_greeting_node()` for dial-in
   - Updated docstring to reflect dial-in workflow
   - Removed unused `ContextStrategy` imports

7. `clients/demo_clinic_beta/patient_questions/flow_definition.py`
   - Same changes as demo_clinic_alpha (removed IVR methods, added `create_greeting_node()`)

**Key Design Decisions:**

- Patient questions workflows are **dial-in only** - no IVR concepts
- Prior auth workflow remains **dial-out only** - uses IVR navigation
- `create_greeting_node()` has `respond_immediately=True` so bot speaks first
- Flow initialization happens in `on_first_participant_joined` handler
- Dial-in handler initializes flow directly (no IVR detection needed)

**Issues Encountered:**
- None

---

## Phase 3: Dial-In Webhook Endpoint

### Phase 3 Actual Implementation

**Files Created:**

1. `backend/api/dialin.py` - New webhook endpoint for incoming Daily PSTN calls
   - `DailyCallData` model: Parses webhook data (From, To, callId, callDomain)
   - `DialinBotRequest` model: Request data sent to bot with call_id/call_domain
   - `call_data_from_request()`: Parses and validates Daily webhook JSON
   - `create_dialin_room()`: Creates Daily room with `sip_caller_phone` using `pipecat.runner.daily.configure()`
   - `start_dialin_bot_production()`: Starts bot via Pipecat Cloud API
   - `start_dialin_bot_local()`: Starts bot via local /start endpoint
   - `POST /dialin-webhook/{client_name}/{workflow_name}`: Main webhook endpoint

**Files Modified:**

1. `backend/main.py`
   - Added import for `dialin` router
   - Registered `dialin.router` with "Dial-In" tag

2. `bot.py`
   - **CRITICAL: Mutually exclusive call type detection**
   - Dial-in detected by presence of `call_id` + `call_domain` (from Daily webhook)
   - Dial-out detected by presence of `phone_number` (no call_id/call_domain)
   - Raises `ValueError` if both are present (impossible state)
   - Passes `dialin_settings` dict to `ConversationPipeline`
   - Uses `caller_phone` from `patient_data` for dial-in display

**Key Design Decisions:**

- **Separate phone numbers for dial-in vs dial-out** - This is enforced at the bot level:
  - Dial-in calls MUST have `call_id` + `call_domain`, MUST NOT have `phone_number`
  - Dial-out calls MUST have `phone_number`, MUST NOT have `call_id` + `call_domain`
  - This prevents any possibility of a dial-in number making outbound calls or vice versa

- **No authentication on webhook** - Daily webhook is public, but:
  - Only Daily knows the URL structure
  - Call validation happens through Daily's call_id/call_domain
  - Could add webhook signature verification later if needed

- **Patient data for dial-in** - Since caller is unknown:
  - `patient_id` is a generated UUID
  - `patient_data` contains `caller_phone`, `called_number`, `call_type`, `created_at`
  - Flow can collect patient info during conversation

**Daily Phone Number Configuration:**

You need TWO separate Daily phone numbers:

1. **Dial-Out Number** (existing `DAILY_PHONE_NUMBER_ID`):
   - Used for `prior_auth` workflow
   - No webhook configured
   - Can ONLY make outbound calls

2. **Dial-In Number** (new purchase required):
   - Used for `patient_questions` workflow
   - Webhook URL configured in Daily dashboard
   - Can ONLY receive inbound calls

**Configuring Dial-In Webhook in Daily Dashboard:**

1. Go to Daily.co Dashboard → Phone Numbers
2. Select your dial-in phone number
3. Set webhook URL to:
   - Production: `https://your-backend.fly.dev/dialin-webhook/{org_slug}/{workflow_name}`
   - Example: `https://api.datasova.com/dialin-webhook/demo_clinic_beta/patient_questions`
4. Save configuration

**Testing Dial-In Locally:**

1. Start backend server: `python app.py` (port 8000)
2. Start bot server: `python bot.py` (port 7860)
3. Use ngrok to expose backend: `ngrok http 8000`
4. Configure Daily webhook to ngrok URL: `https://xxx.ngrok.io/dialin-webhook/demo_clinic_alpha/patient_questions`
5. Call the dial-in phone number from your phone
6. Bot should join and greet the caller

**Testing Dial-Out (verify no regression):**

1. Start backend server: `python app.py`
2. Start bot server: `python bot.py`
3. Use frontend to initiate a prior_auth call
4. Bot should dial out to the patient's phone number

**Issues Encountered:**
- None

---

## Testing Checklist

### Dial-Out (prior_auth)
- [ ] Existing dial-out functionality still works
- [ ] Bot calls out to phone number
- [ ] IVR navigation works
- [ ] Transcript saved

### Dial-In (patient_questions)
- [ ] Webhook receives incoming call data
- [ ] Daily room created with dial-in settings
- [ ] Bot joins and speaks first
- [ ] Conversation flows correctly
- [ ] Transcript saved

### Multi-Client
- [ ] demo_clinic_alpha workflows work
- [ ] demo_clinic_beta workflows work
- [ ] Can run dial-in and dial-out simultaneously

---

## Daily.co Configuration

After implementation, configure your Daily phone number to send webhooks:

1. Go to Daily.co Dashboard → Phone Numbers
2. Select your purchased number
3. Set webhook URL to: `https://your-backend.com/dialin-webhook/{client_name}/{workflow_name}`
4. Example: `https://api.datasova.com/dialin-webhook/demo_clinic_beta/patient_questions`

---

## Environment Variables

### Existing (Dial-Out)
- `DAILY_API_KEY`
- `DAILY_PHONE_NUMBER_ID`

### New (Dial-In)
- No new env vars needed (uses same Daily API key)
- Webhook URL configured in Daily dashboard