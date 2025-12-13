# CLAUDE.md

Healthcare voice AI for automated prior authorization verification. Uses Pipecat AI with Pipecat Flows to orchestrate real-time voice conversations with insurance companies.

## Quick Reference

```bash
# Backend (Terminal 1) - requires venv
source .venv/bin/activate && python app.py     # localhost:8000

# Bot (Terminal 2) - requires venv
source .venv/bin/activate && python bot.py     # localhost:7860

# Frontend
cd frontend && npm start                       # localhost:3000

# Tests
python -m pytest                               # all tests
python -m pytest path/to/test_file.py          # single file
cd frontend && npm test                        # frontend tests
```

## Deployment

```bash
# Backend → Fly.io
fly deploy && fly logs

# Bot → Pipecat Cloud
docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .
pipecatcloud deploy
pipecatcloud agent logs healthcare-voice-ai

# Frontend → Vercel
cd frontend && vercel --prod
```

## Environment Modes

- `ENV=local` → Backend calls `http://localhost:7860/start` (local bot)
- `ENV=production` → Backend calls Pipecat Cloud API

## Tech Stack

- **Backend:** FastAPI, MongoDB (Motor async)
- **Frontend:** React, Shadcn/Radix UI, TailwindCSS
- **Voice:** Pipecat AI + Pipecat Flows
- **Services:** OpenAI (main LLM), Groq (fast classifier), Deepgram Flux (STT), ElevenLabs (TTS), Daily.co (telephony)
- **Observability:** OpenTelemetry + Langfuse

## Key Files

| Purpose | Location |
|---------|----------|
| Flow definitions | `clients/<client>/flow_definition.py` |
| Service configs | `clients/<client>/services.yaml` |
| Pipeline factory | `pipeline/pipeline_factory.py` |
| Pipeline runner | `pipeline/runner.py` |
| IVR handlers | `handlers/ivr.py` |
| Dialin webhook | `backend/api/dialin.py` |
| Dialout endpoint | `backend/api/dialout.py` |
| Bot starting | `backend/server_utils.py` |
| Patient model | `backend/models.py` |

## Call Types

**Dial-Out:** Frontend → `/start-call` → bot starts → `transport.start_dialout()`
- Uses `dialout_targets` in bot body

**Dial-In:** Patient calls → Daily webhook → `/dialin-webhook` → bot joins
- Uses `dialin_settings` (call_id, call_domain, from, to)

## Flow Architecture

Each flow node has:
- `name` - identifier
- `role_messages` - bot persona (persists across nodes)
- `task_messages` - current step instructions
- `functions` - LLM-callable functions → return `(result, next_node)`
- `pre_actions` / `post_actions` - LLM switching, end call, etc.
- `respond_immediately` - bot speaks first or waits

**LLM Switching:** Greeting uses Groq (fast), verification uses OpenAI (function calling). Switch via `pre_actions`.

**IVR Entry Paths:**
1. IVR detected → `IVRNavigator` → verification node
2. Human detected → greeting node → verification node

## Adding New Clients

```
clients/
└── <client_name>/
    ├── flow_definition.py    # <ClientName>Flow class
    └── services.yaml         # ${VAR_NAME} for env vars
```

FlowLoader auto-discovers by naming convention.

## MongoDB Schema (patients collection)

```
patient_name, date_of_birth, insurance_member_id
cpt_code, provider_npi, facility
prior_auth_status: "Pending" | "Approved" | "Denied"
call_status: "Not Started" | "In Progress" | "Completed"
reference_number, call_transcript
last_call_session_id, last_call_timestamp
```

## Environment Variables

**Backend (app.py):**
```bash
MONGO_URI, JWT_SECRET_KEY, ALLOWED_ORIGINS, DAILY_API_KEY
PIPECAT_API_KEY, PIPECAT_AGENT_NAME  # production only
```

**Bot (bot.py):**
```bash
OPENAI_API_KEY, GROQ_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY
DAILY_API_KEY, DAILY_PHONE_NUMBER_ID, MONGO_URI
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY  # optional
```

## Implementation Plans

**IMPORTANT:** Before implementing features from `docs/*.md`:
1. Read the plan first - contains architecture decisions
2. Files marked `← UPDATE` or `← ADD` indicate where to change
3. Follow implementation order - dependencies matter
4. Run verification checklist before considering done

Current: `docs/start-bots.md`

## Project-Specific Patterns

**Conversation logic:** Edit `clients/<client>/flow_definition.py`

**New flow nodes:**
1. Add `create_<name>_node()` method
2. Define `role_messages` + `task_messages`
3. Add function handlers returning `(result, next_node)`
4. Update transitions from other nodes

**New LLM functions:**
```python
async def my_function(self, flow_manager, ...):
    # business logic
    return result, self.create_next_node()  # or None to stay
```

**Core pipeline changes:** Affect all clients - test thoroughly. Key files: `pipeline/pipeline_factory.py`, `pipeline/runner.py`, `handlers/ivr.py`

**Frontend:** Use Shadcn/Radix components from `frontend/src/components/ui/`, follow patterns in `frontend/src/components/workflows/`

## Deepgram Flux Tuning

```yaml
stt:
  model: flux-general-en
  eager_eot_threshold: 0.55   # 0.4-0.7 (lower = faster, more interrupts)
  eot_threshold: 0.65         # 0.5-0.8 (lower = faster responses)
  eot_timeout_ms: 3500
  keyterm: ["prior authorization", "CPT code", "NPI"]
```

IVR: `eager_eot: 0.4-0.5`, `eot: 0.6`
Conversation: `eager_eot: 0.5-0.6`, `eot: 0.65-0.7`

