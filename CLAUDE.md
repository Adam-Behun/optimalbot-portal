# CLAUDE.md

## Quick Reference

```bash
# Local development
./run.sh                          # validates, starts all services on localhost
# First-time setup
./setup-local.sh                  # create .venv with uv
cd frontend && npm install        # install frontend deps
cd ../marketing && npm install    # install marketing deps
```

## Deployment

```bash
# Deploy to test
./deploy.sh test
./deploy.sh test backend
./deploy.sh test bot

# Deploy to production
./deploy.sh prod

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
- **Services:** OpenAI (main LLM), Groq (fast classifier), Deepgram Flux (STT), Cartesia (TTS), Daily.co (telephony)
- **Observability:** OpenTelemetry + Langfuse

## Key Files

| Purpose | Location |
|---------|----------|
| Flow definitions | `clients/<org>/<workflow>/flow_definition.py` |
| Service configs | `clients/<org>/<workflow>/services.yaml` |
| Pipeline factory | `pipeline/pipeline_factory.py` |
| Transport handlers | `handlers/transport.py` |
| Dialin webhook | `backend/api/dialin.py` |
| Dialout endpoint | `backend/api/dialout.py` |
| Bot starting | `backend/server_utils.py` |
| Patient model | `backend/models/patient.py` |

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

**Core pipeline changes:** Affect all clients - test thoroughly. Key files: `pipeline/pipeline_factory.py`, `handlers/transport.py`

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

