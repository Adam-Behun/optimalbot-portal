# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyRobot is a healthcare voice AI system for automated prior authorization verification. It uses Pipecat AI to orchestrate real-time voice conversations with insurance companies, navigating IVR systems and conducting eligibility verification calls on behalf of medical providers.

**Tech Stack:**
- Backend: FastAPI (Python), MongoDB (Motor async driver)
- Frontend: React (Create React App)
- Voice Pipeline: Pipecat AI framework
- Services: OpenAI (LLM), Deepgram (STT), ElevenLabs (TTS), Daily.co (telephony)
- Observability: OpenTelemetry + Langfuse tracing

## Development Commands

### Backend Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (port 8000)
python app.py

# Run tests
python -m pytest

# Single test file
python -m pytest path/to/test_file.py
```

### Frontend Development

```bash
cd frontend

# Install dependencies
npm install

# Development server (port 3000)
npm start

# Production build
npm run build

# Run tests
npm test
```

### Full Stack

The backend serves the frontend from `frontend/build/` when built. In production, run only `python app.py` after building the frontend.

## Architecture Overview

### Schema-Driven Conversation Engine

The system uses a **declarative YAML configuration** approach where each client (e.g., `prior_auth`) defines their conversation flow via three files in `clients/<client_name>/`:

1. **`schema.yaml`** - State machine definition with states, transitions, voice persona, data schema
2. **`prompts.yaml`** - Jinja2 prompt templates for each state (system/task sections)
3. **`services.yaml`** - Service configuration (STT/TTS/LLM/transport providers, API keys via env vars)

This architecture allows adding new use cases by creating new client directories without code changes.

### Core Components

**`core/`** - Conversation engine
- `schema_parser.py` - Pydantic models parsing YAML into validated objects (`ConversationSchema`)
- `client_loader.py` - Loads all 3 YAML files and creates `ClientConfig` with helpers
- `prompt_renderer.py` - Jinja2 template renderer with precompilation and caching
- `data_formatter.py` - Formats patient data according to schema preformat rules (spell out IDs, natural speech dates)
- `state_manager.py` - Handles state transitions (keyword-based, LLM-directed via `<next_state>` tags, event-driven)
- `context.py` - Manages conversation context and patient data access per state

**`pipeline/`** - Pipecat pipeline assembly
- `pipeline_factory.py` - Builds Pipecat pipeline from client config (services → processors → transport)
- `runner.py` - `ConversationPipeline` class orchestrating full call lifecycle with OpenTelemetry tracing
- `audio_processors.py` - Custom audio processors (resampling, empty audio dropping, state tag stripping)

**`services/`** - Service instantiation
- `service_factory.py` - Creates Pipecat service instances (STT, TTS, LLM, Daily transport, VAD) from parsed YAML configs

**`handlers/`** - Event handlers for pipeline
- `ivr.py` - IVR detection and navigation status handlers
- `transcript.py` - Transcript collection and MongoDB persistence
- `transport.py` - Daily.co dialout handlers
- `voicemail.py` - Voicemail detection
- `function.py` - LLM function call handlers

**`backend/`** - Database layer
- `models.py` - `AsyncPatientRecord` class with Motor async operations
- `functions.py` - LLM function definitions (`update_prior_auth_status`) and handlers

### Request Flow

1. **POST /start-call** → Fetch patient from MongoDB → Create Daily.co room → Initialize `ConversationPipeline`
2. **Pipeline initialization:**
   - `ClientLoader` loads schema/prompts/services for client
   - `PipelineFactory` builds Pipecat pipeline with all services
   - Handlers registered (dialout, IVR, transcript, function calls)
   - OpenTelemetry tracing enabled with Langfuse exporter
3. **Call execution:**
   - Pipeline runs async task
   - Daily.co places outbound call
   - IVR navigator detects and navigates phone menus
   - State transitions occur based on schema rules
   - LLM function calls update patient records in MongoDB
   - Full transcript saved to database on call completion
4. **Monitoring:** OpenTelemetry spans sent to Langfuse for observability

### State Transition Types

The system supports three transition mechanisms:

1. **Event-driven** - Triggered by pipeline events (IVR detection, human answered, IVR completed/stuck)
2. **Keyword-based** - Schema defines triggers matching user utterances (deprecated, only used for legacy states)
3. **LLM-directed** - LLM includes `<next_state>state_name</next_state>` tag in response when ready to transition (used for verification state)

States specify `llm_directed: true` in schema to enable LLM control. State manager validates transitions against `allowed_transitions`.

### Data Access Control

Each state in `schema.yaml` declares `data_access` array listing which patient fields are available. `PromptRenderer` filters context when rendering prompts, ensuring LLM only sees relevant data per state.

Global instructions from `_global_instructions` in `prompts.yaml` are injected into every state's prompt via `{{ _global_instructions }}` placeholder.

## Client Configuration Structure

Each client directory (`clients/<client_name>/`) must contain:

```
clients/
└── prior_auth/
    ├── schema.yaml       # State machine, voice config, data schema
    ├── prompts.yaml      # Jinja2 templates (state prompts + utilities)
    └── services.yaml     # Service providers and settings
```

**Adding a new client:**
1. Create directory under `clients/`
2. Copy and modify the three YAML files
3. No code changes required - engine is fully generic

**Environment variables** in `services.yaml` use `${VAR_NAME}` syntax (substituted by `ClientLoader`).

## MongoDB Schema

**`patients` collection:**
- `_id` (ObjectId) - Auto-generated
- `patient_name`, `date_of_birth`, `insurance_member_id`, etc. - Patient demographics
- `cpt_code`, `provider_npi`, `facility` - Procedure details
- `prior_auth_status` - "Pending" | "Approved" | "Denied"
- `call_status` - "Not Started" | "In Progress" | "Completed"
- `reference_number` - Authorization reference from insurance
- `call_transcript` - Full conversation transcript object
- `last_call_session_id`, `last_call_timestamp` - Call metadata
- `created_at`, `updated_at` - Timestamps

## Key Implementation Details

### OpenTelemetry Integration

Tracing is configured in `app.py` on startup:
- Langfuse OTLP exporter with Basic auth (public + secret keys)
- Pipecat's `setup_tracing()` called with custom exporter
- `PipelineTask` created with `enable_tracing=True` and `enable_turn_tracking=True`
- Conversation ID set to session_id for correlation
- Custom span attributes: patient.id, phone.number, client.name

### Prompt Rendering Performance

`PromptRenderer` precompiles all Jinja2 templates on initialization for <5ms render times during calls. Templates cached in `_cache` dictionary by `state.section` keys.

### IVR Navigation

`IVRNavigator` (Pipecat extension) detects IVR systems and makes menu selections via DTMF tones. State manager listens to `on_ivr_status_changed` events to transition between connection → ivr_navigation → greeting states.

### Function Calling

LLM can call `update_prior_auth_status(patient_id, status, reference_number)` during verification state. Function registered in `service_factory.py` and handled in `backend/functions.py` with direct MongoDB updates.

### Transcript Persistence

`transcript.py` handler collects all user/assistant messages during call and saves to MongoDB on call completion with session metadata.

## Environment Variables Required

```
OPENAI_API_KEY          # OpenAI API key for LLM
DEEPGRAM_API_KEY        # Deepgram for STT
ELEVENLABS_API_KEY      # ElevenLabs for TTS
DAILY_API_KEY           # Daily.co for telephony
DAILY_PHONE_NUMBER_ID   # Daily.co outbound phone number ID
MONGO_URI               # MongoDB connection string
LANGFUSE_PUBLIC_KEY     # Langfuse observability (public key)
LANGFUSE_SECRET_KEY     # Langfuse observability (secret key)
LANGFUSE_HOST           # Langfuse host URL
```

Optional:
- `DEBUG=true` - Enable debug mode
- `OTEL_CONSOLE_EXPORT=true` - Print OpenTelemetry spans to console
- `PORT=8000` - Server port (default 8000)

## Testing Strategy

Tests use pytest with async support (`pytest-asyncio`). Test files should follow naming convention `test_*.py`.

When testing pipeline components, mock Daily.co rooms and MongoDB connections to avoid external dependencies.

## Code Modification Guidelines

**When modifying conversation logic:**
- Prefer editing YAML configs over code changes
- State transitions should be defined in `schema.yaml`
- Prompt modifications go in `prompts.yaml`
- Service settings go in `services.yaml`

**When modifying core engine:**
- Changes to `core/` affect all clients - test thoroughly
- Update Pydantic models in `schema_parser.py` if changing YAML schema
- Prompt renderer cache must be invalidated if template loading changes
- State manager transitions must respect schema's `allowed_transitions`

**When adding new services:**
- Add factory method in `service_factory.py`
- Define config schema in services.yaml
- Register in `pipeline_factory.py` assembly

**Adding new LLM functions:**
- Define schema in `backend/functions.py` → `PATIENT_TOOLS`
- Implement handler function
- Register in `service_factory.create_llm()`
- Reference in state's `functions` array in schema.yaml
