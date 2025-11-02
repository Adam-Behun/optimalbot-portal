# MyRobot - Codebase Explained

**Healthcare Voice AI System for Prior Authorization Verification**

---

## What It Does

MyRobot automates prior authorization calls to insurance companies. It:
1. Places outbound phone calls to insurance IVR systems
2. Navigates phone menus (detects options, presses DTMF tones)
3. Conducts eligibility verification conversations with representatives
4. Updates patient records with authorization status and reference numbers
5. Records full transcripts for audit compliance

---

## Architecture Overview

### Three Separate Deployments

```
┌─────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│   FRONTEND      │────▶│    BACKEND      │────▶│      BOT         │
│   (Vercel)      │     │    (Fly.io)     │     │ (Pipecat Cloud)  │
│                 │     │                 │     │                  │
│ React UI        │     │ FastAPI REST    │     │ Voice AI Engine  │
│ Patient Forms   │     │ JWT Auth        │     │ Daily.co Calls   │
│ Call Triggers   │     │ MongoDB Client  │     │ IVR Navigation   │
└─────────────────┘     └─────────────────┘     └──────────────────┘
                               │                         │
                               └────────┬────────────────┘
                                        ▼
                                  ┌──────────┐
                                  │ MongoDB  │
                                  │  Atlas   │
                                  └──────────┘
```

**Why distributed?**
- Backend: Handles auth, CRUD operations, session management
- Bot: Heavy voice processing (can't run on Fly.io, needs Pipecat Cloud)
- Frontend: Static React app (fast CDN delivery via Vercel)

---

## How It Works (Request Flow)

### 1. User Starts a Call (Frontend)
```
User clicks "Start Call" → POST /api/calls/start
```

### 2. Backend Creates Session (Fly.io)
```python
# app.py on Fly.io
- Fetch patient record from MongoDB
- Create session with Pipecat Cloud API
- Pass patient data to bot via SessionParams
- Return session info to frontend
```

### 3. Bot Executes Call (Pipecat Cloud)
```python
# bot.py on Pipecat Cloud
async def bot(args: DailyRunnerArguments):
    # Load client configuration (YAML files)
    client_config = ClientLoader.load("prior_auth")

    # Build voice pipeline
    pipeline = PipelineFactory.build(client_config)

    # Register event handlers
    setup_ivr_handlers()      # IVR detection & navigation
    setup_transcript_handler() # Record conversation
    setup_dialout_handlers()   # Daily.co connection
    setup_function_call_handler() # LLM function calls

    # Place outbound call
    await transport.dial_out(phone_number)

    # State machine handles conversation
    # - IVR navigation → greeting → verification → closing
    # - LLM generates responses using prompts from YAML
    # - Transitions based on events, keywords, or LLM tags

    # On completion: save transcript to MongoDB
```

---

## Core Components

### Backend (`/backend`) - FastAPI REST API

**Purpose:** Authentication, patient CRUD, session management

**Key Files:**
- `app.py` - Entry point (30 lines)
- `main.py` - FastAPI app factory
- `models.py` - Patient Pydantic models
- `api/auth.py` - JWT login/signup
- `api/patients.py` - Patient CRUD endpoints
- `api/calls.py` - Session creation (calls Pipecat Cloud)
- `sessions.py` - MongoDB session tracking
- `audit.py` - HIPAA audit logging

**Tech:** FastAPI, Motor (async MongoDB), Pydantic, JWT

---

### Core Engine (`/core`) - Schema-Driven Conversation System

**Purpose:** Convert YAML configs into executable conversation state machines

**How it works:**
1. `client_loader.py` - Loads 3 YAML files for a client
2. `schema_parser.py` - Validates YAML into Pydantic models
3. `prompt_renderer.py` - Compiles Jinja2 templates (prompts)
4. `state_manager.py` - Handles state transitions
5. `data_formatter.py` - Formats patient data for speech
6. `context.py` - Manages conversation context per state

**State Transitions:**
- **Event-driven:** IVR detected → navigate, Human answered → greet
- **Keyword-based:** User says "approved" → transition to closing
- **LLM-directed:** LLM outputs `<next_state>verification</next_state>` tag

**Data Access Control:** Each state declares which patient fields it can access (security + focus)

---

### Client Configs (`/clients/prior_auth`) - YAML Configuration

**3 files define entire conversation flow (no code changes needed):**

#### 1. `schema.yaml` - State Machine Definition
```yaml
states:
  - name: greeting
    voice:
      model: gpt-4o-mini
      voice_id: xyz
    allowed_transitions: [verification, closing]
    data_access: [patient_name, insurance_company]
```

#### 2. `prompts.yaml` - Jinja2 Templates
```yaml
greeting:
  system: You are a healthcare assistant...
  task: |
    Greet the representative.
    Patient name: {{ patient.patient_name }}
```

#### 3. `services.yaml` - Service Configuration
```yaml
stt:
  provider: deepgram
  model: flux-general-en
  eager_eot_threshold: 0.55
tts:
  provider: elevenlabs
  voice_id: ${ELEVENLABS_VOICE_ID}
llm:
  provider: openai
  model: gpt-4o
```

**To add a new use case:** Create new client directory with these 3 files. Done!

---

### Pipeline (`/pipeline`) - Pipecat Orchestration

**Purpose:** Assemble Pipecat services into working voice pipeline

- `pipeline_factory.py` - Creates STT → LLM → TTS → Transport chain
- `runner.py` - `ConversationPipeline` class (main orchestrator)
  - Registers handlers
  - Manages call lifecycle
  - Sends OpenTelemetry traces to Langfuse
- `audio_processors.py` - Custom audio processing (resampling, tag stripping)

---

### Handlers (`/handlers`) - Event Subscribers

**Purpose:** React to pipeline events

| Handler | Event | Action |
|---------|-------|--------|
| `ivr.py` | IVR detected | Navigate menu with DTMF tones |
| `transport.py` | Connection state changed | Handle Daily.co dialout |
| `transcript.py` | Each user/assistant turn | Collect messages, save to MongoDB |
| `function.py` | LLM function call | Update patient record in MongoDB |

---

### Services (`/services`) - Service Factory

**Purpose:** Instantiate Pipecat services from YAML configs

- Creates Deepgram STT (Flux model with turn detection)
- Creates ElevenLabs TTS
- Creates OpenAI LLM with function definitions
- Creates Daily.co transport for telephony

**Key Feature:** Deepgram Flux handles turn detection internally (no separate VAD needed)

---

### Frontend (`/frontend`) - React TypeScript UI

**Purpose:** Patient management interface

**Key Pages:**
- Login/Signup
- Patient List (table with search/filter)
- Add Patient Form
- Patient Detail (view/edit + transcript)
- Start Call button → triggers backend API

**Tech:** React 18, TypeScript, TailwindCSS, Shadcn UI, Vite, Axios

---

### Evaluations (`/evals`) - Testing Framework

**Purpose:** Test pipeline components systematically

**Structure:**
```
evals/
├── ivr/          ✅ Complete (framework + test cases + grading)
├── llm/          ✅ Framework complete (no test runs yet)
├── stt/          ❌ Placeholder
├── tts/          ❌ Placeholder
└── e2e/          ❌ Placeholder
```

**IVR Evals:** Generate synthetic IVR scenarios, test navigation, grade success rate

---

## Key Technologies

### Backend
- **FastAPI** - REST API framework
- **Motor** - Async MongoDB driver
- **Pydantic** - Data validation
- **JWT** - Authentication

### Bot
- **Pipecat AI** - Voice pipeline orchestration
- **Deepgram Flux** - STT with turn detection (no separate VAD)
- **ElevenLabs** - Text-to-speech
- **OpenAI** - LLM (GPT-4)
- **Daily.co** - Telephony/WebRTC
- **PyYAML** - Config parsing
- **Jinja2** - Prompt templating

### Frontend
- **React 18** - UI framework
- **TypeScript** - Type safety
- **TailwindCSS** - Styling
- **Shadcn UI** - Component library
- **Vite** - Build tool

### Observability
- **OpenTelemetry** - Distributed tracing
- **Langfuse** - Trace visualization

---

## Development Commands

### Backend (Local)
```bash
source venv/bin/activate
python app.py              # http://localhost:8000
python -m pytest           # Run tests
```

### Frontend (Local)
```bash
cd frontend
npm install
npm start                  # http://localhost:3000
```

### Deploy Bot
```bash
# Test environment
./deploy-test.sh

# Production environment
./deploy-prod.sh
```

### Deploy Backend
```bash
fly deploy                 # Fly.io
```

### Deploy Frontend
```bash
cd frontend
vercel --prod              # Vercel
```

---

## Environment Variables

### Backend (.env on Fly.io)
```bash
PIPECAT_API_KEY            # Pipecat Cloud API key
PIPECAT_AGENT_NAME         # healthcare-voice-ai (prod) or healthcare-voice-ai-test
MONGO_URI                  # MongoDB connection string
JWT_SECRET_KEY             # JWT signing secret
ALLOWED_ORIGINS            # CORS origins
LANGFUSE_PUBLIC_KEY        # Optional: observability
LANGFUSE_SECRET_KEY        # Optional: observability
```

### Bot (Pipecat Cloud secret set: healthcare-secrets)
```bash
OPENAI_API_KEY             # OpenAI for LLM
DEEPGRAM_API_KEY           # Deepgram for STT
ELEVENLABS_API_KEY         # ElevenLabs for TTS
DAILY_API_KEY              # Daily.co for telephony
DAILY_PHONE_NUMBER_ID      # Daily.co outbound phone number
MONGO_URI                  # MongoDB (for LLM function calls)
LANGFUSE_PUBLIC_KEY        # Optional: observability
LANGFUSE_SECRET_KEY        # Optional: observability
```

---

## Critical HIPAA Compliance TODOs

**Status:** ⚠️ Not production-ready for PHI

### Outstanding Issues
1. ❌ Business Associate Agreements (BAAs) - Not verified with vendors
2. ❌ Call Recording Disclosure - No disclosure system before recording
3. ❌ Data Minimization - Entire patient record sent to Pipecat Cloud
4. ❌ Backup Verification - MongoDB Atlas restore procedures not tested
5. ❌ Disaster Recovery Plan - No documented failover procedures
6. ❌ Data Retention Policy - No TTL enforcement on patient records
7. ❌ Incident Response Plan - No PHI breach response procedures
8. ❌ Audit Log Enforcement - TTL indexes and retention not verified
9. ❌ Testing Coverage - Need unit tests, integration tests, E2E tests
10. ❌ Monitoring/Alerting - No production monitoring or cost tracking

**Next Steps:** Address these before handling real PHI in production.

---

## File Statistics

| Component | Files | Lines of Code | Size |
|-----------|-------|---------------|------|
| Backend | 17 | ~3,000 | 284KB |
| Core Engine | 6 | ~700 | 116KB |
| Pipeline | 3 | - | 68KB |
| Handlers | 5 | - | 88KB |
| Services | 1 | - | 24KB |
| Frontend | 30+ | ~3,906 | 4MB |
| Evals | 15+ | ~1,938 | 492KB |
| **Total Code** | **77+** | **~9,500** | **~1GB** |

---

## Adding a New Use Case

**Example:** Add "Appointment Scheduling" client

1. Create directory: `clients/appointment_scheduling/`
2. Copy and modify 3 YAML files:
   - `schema.yaml` - Define states (greeting → collect_info → confirm → closing)
   - `prompts.yaml` - Write state prompts
   - `services.yaml` - Configure services (can use same as prior_auth)
3. Update frontend to select client when starting call
4. **No code changes required!**

---

## Project Structure (Quick Reference)

```
MyRobot/
├── app.py                    # Backend entry point
├── bot.py                    # Bot entry point
├── backend/                  # FastAPI REST API
├── core/                     # Conversation engine (generic)
├── pipeline/                 # Pipecat orchestration
├── handlers/                 # Event subscribers
├── services/                 # Service factory
├── clients/prior_auth/       # YAML configs (state machine)
├── evals/                    # Testing framework
├── frontend/                 # React UI
├── deploy-prod.sh            # Deploy bot to production
├── deploy-test.sh            # Deploy bot to test
├── Dockerfile.api            # Backend container
├── Dockerfile.bot            # Bot container
├── fly.toml                  # Fly.io config
├── pcc-deploy.toml           # Pipecat Cloud config (prod)
├── pcc-deploy.test.toml      # Pipecat Cloud config (test)
└── requirements.txt          # Python dependencies
```

---

## Key Design Principles

### 1. Schema-Driven Configuration
YAML files define conversation flow. Add new use cases without touching code.

### 2. Separation of Concerns
- Backend: Auth, CRUD, sessions
- Bot: Voice AI processing
- Frontend: User interface
- Each deployed independently

### 3. Event-Driven Architecture
Handlers subscribe to pipeline events (IVR detected, human answered, turn completed)

### 4. State Machine Conversations
Well-defined states with clear transitions. LLM operates within state context.

### 5. Data Access Control
Each state declares which patient fields it needs. Security + focus.

---

## Useful Links

- **Backend API Docs:** http://localhost:8000/docs (Swagger)
- **Frontend:** http://localhost:3000
- **Production Backend:** https://prior-auth-agent-v2.fly.dev
- **Pipecat Cloud:** https://pipecatcloud.com
- **Langfuse Traces:** (configure LANGFUSE_HOST)

---

**Last Updated:** November 2, 2025
