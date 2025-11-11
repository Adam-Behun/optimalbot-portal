# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyRobot is a healthcare voice AI system for automated prior authorization verification. It uses Pipecat AI with Pipecat Flows to orchestrate real-time voice conversations with insurance companies, navigating IVR systems and conducting eligibility verification calls on behalf of medical providers.

**Tech Stack:**
- Backend: FastAPI (Python), MongoDB (Motor async driver)
- Frontend: React (Create React App)
- Voice Pipeline: Pipecat AI framework with Pipecat Flows
- Services: OpenAI (LLM), Groq (fast classifier LLM), Deepgram Flux (STT), ElevenLabs (TTS), Daily.co (telephony)
- Observability: OpenTelemetry + Langfuse tracing

## Development Commands

### Local Development Mode

The system supports **dual-mode operation** controlled by the `ENV` environment variable:

- **`ENV=local`**: Backend calls local bot server for fast iteration and debugging
- **`ENV=production`**: Backend calls Pipecat Cloud API (production deployment)

**Running Locally (Two Terminals Required):**

**Terminal 1 - Backend API Server:**
```bash
# Ensure ENV=local in .env file
python app.py
# Runs on http://localhost:8000
```

**Terminal 2 - Bot Server:**
```bash
# Start local bot server
python bot.py
# Runs on http://localhost:7860
```

**How It Works:**
1. Frontend → Backend (port 8000) → `/start-call`
2. Backend creates Daily room via `pipecat.runner.daily.configure()`
3. Backend calls local bot server → `http://localhost:7860/start`
4. Local bot joins Daily room and makes outbound call
5. Full debugging capabilities with breakpoints and real-time logs

**Benefits of Local Mode:**
- ✅ No deployment needed for testing changes
- ✅ Set breakpoints and inspect variables
- ✅ See full logs in real-time
- ✅ Test IVR flows with real outbound calls
- ✅ Faster iteration cycle

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

### Deployment Architecture

**Production deployment uses three separate services:**
- **Backend:** Deployed to Fly.io (runs `app.py` - FastAPI REST API only)
- **Bot:** Deployed to Pipecat Cloud (runs `bot.py` - voice AI conversation orchestration)
- **Frontend:** Deployed to Vercel (React application)

### Deployment Commands

**Backend (Fly.io):**
```bash
# Deploy backend to Fly.io
fly deploy

# View logs
fly logs

# Check status
fly status
```

**Bot (Pipecat Cloud):**
```bash
# Build and push Docker image
docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .

# Deploy to Pipecat Cloud
pipecatcloud deploy

# View logs
pipecatcloud agent logs healthcare-voice-ai

# Check status
pipecatcloud agent list
```

**Frontend (Vercel):**
```bash
cd frontend

# Deploy to production
vercel --prod

# Deploy to preview
vercel
```

## Architecture Overview

### Pipecat Flows Conversation Engine

The system uses **Pipecat Flows** (official Pipecat framework) for structured conversation management. Each client (e.g., `prior_auth`) defines their conversation flow via:

1. **`flow_definition.py`** - Python class defining conversation nodes, transitions, and business logic
2. **`services.yaml`** - Service configuration (STT/TTS/LLM/transport providers, API keys via env vars)

This architecture allows adding new use cases by creating new client directories with minimal code.

### Core Components

**`core/`** - Flow loading infrastructure
- `flow_loader.py` - Dynamically loads client-specific flow classes

**`clients/<client_name>/`** - Client-specific conversation definitions
- `flow_definition.py` - Flow class with node factories and function handlers
- `services.yaml` - Service configurations

**`pipeline/`** - Pipecat pipeline assembly
- `pipeline_factory.py` - Builds Pipecat pipeline with FlowManager, LLMSwitcher, and IVRNavigator
- `runner.py` - `ConversationPipeline` class orchestrating full call lifecycle with OpenTelemetry tracing
- `audio_processors.py` - Custom audio processors (resampling, empty audio dropping, code formatting)

**`services/`** - Service instantiation
- `service_factory.py` - Creates Pipecat service instances (STT, TTS, LLM, Daily transport) from YAML configs

**`handlers/`** - Event handlers for pipeline
- `ivr.py` - IVR detection handlers that transition to flow nodes
- `transcript.py` - Transcript collection and MongoDB persistence
- `transport.py` - Daily.co dialout handlers

**`backend/`** - Database layer
- `models.py` - `AsyncPatientRecord` class with Motor async operations
- `functions.py` - Supervisor transfer handler

### Request Flow (Pipecat Cloud Architecture)

1. **POST /start-call** (Backend - app.py on Fly.io):
   - Fetch patient from MongoDB
   - Create session record
   - Call Pipecat Cloud API via `pipecatcloud.session.Session()`
   - Pass patient data to Pipecat Cloud via `SessionParams.data`
   - Return session info to frontend

2. **Bot execution** (Bot - bot.py on Pipecat Cloud):
   - Pipecat Cloud invokes `async def bot(args: DailyRunnerArguments)`
   - `FlowLoader` loads client flow class
   - `PipelineFactory` builds pipeline with FlowManager + LLMSwitcher + IVRNavigator
   - Handlers registered (dialout, IVR, transcript)
   - Daily.co places outbound call via transport.dial_out()
   - IVRNavigator detects and navigates phone menus OR detects human conversation
   - On IVR complete or conversation detected → FlowManager initialized with appropriate node
   - Flow nodes handle conversation logic with automatic LLM switching
   - LLM function calls update patient records in MongoDB
   - Full transcript saved to database on call completion

3. **Monitoring:** OpenTelemetry spans sent to Langfuse for observability

### Pipecat Flows Architecture

**Flow Nodes:**
Each node in the conversation flow defines:
- `name` - Node identifier
- `role_messages` - System messages defining bot persona (set once, persists across nodes)
- `task_messages` - Instructions for the current conversation step
- `functions` - Available function calls for this node (LLM can call these)
- `pre_actions` - Actions to execute before LLM runs (e.g., switch LLMs)
- `post_actions` - Actions to execute after LLM completes (e.g., end conversation)
- `respond_immediately` - Whether bot speaks first or waits for user

**Function Handlers:**
Functions in flow nodes serve dual purposes:
1. Execute business logic (e.g., update database)
2. Transition to next node (return `(result, next_node)`)

**LLM Switching:**
- Fast classifier LLM (Groq Llama 3.1 8B @ 560 t/s) for greeting
- Main LLM (OpenAI GPT-4o-mini) for verification with function calling
- Automatic switching via `pre_actions` in node definitions
- Uses Pipecat's `LLMSwitcher` with `ServiceSwitcherStrategyManual`

### IVR Navigator Integration

**Two Entry Paths:**

1. **IVR System Detected** → `IVRNavigator` navigates menus → transitions to verification node
2. **Human Conversation Detected** → FlowManager initialized with greeting node → transitions to verification

**Event Handlers:**
- `on_conversation_detected` - Human answered, initialize flow with greeting
- `on_ivr_status_changed(COMPLETED)` - IVR complete, transition to verification
- `on_ivr_status_changed(STUCK)` - IVR failed, end call

VAD parameters adjusted to `stop_secs=0.8` when transitioning from IVR to conversation.

### Flow Node Types (Prior Auth Client)

1. **Greeting Node**
   - Uses classifier LLM (fast)
   - Greets human, introduces as Virtual Assistant
   - Extracts representative name from conversation history
   - Transitions to verification via `proceed_to_verification` function

2. **Verification Node**
   - Uses main LLM (with function calling)
   - Provides patient information (name, DOB, member ID, CPT code, NPI)
   - Collects authorization status and reference number
   - Functions: `update_prior_auth_status`, `request_supervisor`, `proceed_to_closing`
   - Transitions to supervisor_confirmation or closing

3. **Supervisor Confirmation Node**
   - Confirms supervisor transfer request
   - Functions: `dial_supervisor`, `return_to_verification`

4. **Closing Node**
   - Checks if anything else needed
   - Functions: `return_to_verification`, `end_call`
   - Ends conversation with goodbye

## Client Configuration Structure

Each client directory (`clients/<client_name>/`) must contain:

```
clients/
└── prior_auth/
    ├── flow_definition.py    # Flow class with nodes and handlers
    └── services.yaml         # Service providers and settings
```

**Adding a new client:**
1. Create directory under `clients/`
2. Create `flow_definition.py` with `<ClientName>Flow` class
3. Copy and modify `services.yaml`
4. `FlowLoader` automatically discovers the class by naming convention

**Environment variables** in `services.yaml` use `${VAR_NAME}` syntax (substituted by `PipelineFactory`).

### Flow Definition Structure

```python
class PriorAuthFlow:
    def __init__(self, patient_data, flow_manager, main_llm, classifier_llm):
        self.patient_data = patient_data
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm

    def create_greeting_node(self) -> NodeConfig:
        return NodeConfig(
            name="greeting",
            role_messages=[...],
            task_messages=[...],
            functions=[self.proceed_to_verification],
            respond_immediately=False,
            pre_actions=[{"type": "function", "handler": self._switch_to_classifier_llm}]
        )

    async def proceed_to_verification(self, flow_manager):
        return None, self.create_verification_node()
```

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

Tracing is configured in `bot.py` on startup:
- Langfuse OTLP exporter with Basic auth (public + secret keys)
- Pipecat's `setup_tracing()` called with custom exporter
- `PipelineTask` created with `enable_tracing=True` and `enable_turn_tracking=True`
- Conversation ID set to session_id for correlation
- Custom span attributes: patient.id, phone.number, client.name

### LLM Switching for Performance

**Two-LLM Architecture:**
- **Classifier LLM** (Groq Llama 3.1 8B): Ultra-fast greeting generation (~200-500ms)
  - 560 tokens/second throughput
  - No function calling
  - Minimal prompt complexity

- **Main LLM** (OpenAI GPT-4o-mini): Full-featured verification (~1-2s)
  - Function calling enabled
  - Complex reasoning
  - Full patient data access

**Automatic Switching:**
Implemented via `pre_actions` in node definitions. When transitioning to a node, the `pre_action` handler pushes `ManuallySwitchServiceFrame` upstream from the context aggregator to ensure the switch happens before LLM inference.

### Deepgram Flux STT

The system uses **Deepgram Flux** for speech-to-text with built-in turn detection (no external VAD required).

**Features:**
- **Model:** `flux-general-en` (general-purpose English model)
- **Built-in turn detection:** EagerEndOfTurn and EndOfTurn events provide low-latency turn detection
- **EagerEndOfTurn:** Detects potential turn ends early, allowing LLM processing to start before speaker finishes (reduces latency by 500-1500ms)
- **No Silero VAD needed:** Flux handles turn detection internally, eliminating ~1.5GB PyTorch dependency

**Configuration Parameters:**

Configure in `clients/<client_name>/services.yaml`:

```yaml
stt:
  api_key: ${DEEPGRAM_API_KEY}
  model: flux-general-en
  eager_eot_threshold: 0.55    # 0.4-0.7 range
  eot_threshold: 0.65          # 0.5-0.8 range
  eot_timeout_ms: 3500         # Max ms to wait
  keyterm: ["prior authorization", "CPT code", "NPI"]
  tag: ["production", "prior-auth"]
```

**Parameter Tuning Guide:**
- `eager_eot_threshold`: Lower = more aggressive interruption detection (faster, more LLM calls); higher = more conservative
- `eot_threshold`: Lower = turns end sooner (faster responses, more interruptions); higher = turns end later (complete utterances)
- `eot_timeout_ms`: Maximum time after speech stops before forcing turn end regardless of confidence
- `keyterm`: List of specialized terms to improve recognition accuracy

**Recommended Settings:**
- **IVR menus**: `eager_eot: 0.4-0.5`, `eot: 0.6`
- **Natural conversations**: `eager_eot: 0.5-0.6`, `eot: 0.65-0.7`
- **Complex info collection**: `eager_eot: 0.6-0.7`, `eot: 0.75+`

### Transcript Persistence

`transcript.py` handler collects all user/assistant messages during call and saves to MongoDB on call completion with session metadata.

## Environment Variables Required

### Environment Mode Control

**Core Variables:**
```bash
ENV=local                # "local" for development, "production" for deployment
LOCAL_BOT_URL=http://localhost:7860  # Local bot server URL (local mode only)
BOT_PORT=7860            # Port for local bot server (local mode only)
```

### Backend Environment Variables (app.py)

**Required (All Modes):**
```bash
MONGO_URI                # MongoDB connection string
JWT_SECRET_KEY           # JWT token signing secret (min 32 chars)
ALLOWED_ORIGINS          # CORS origins (comma-separated)
DAILY_API_KEY            # Daily.co API key (for creating rooms in local mode)
```

**Required (Production Mode Only):**
```bash
PIPECAT_API_KEY          # Pipecat Cloud API key
PIPECAT_AGENT_NAME       # Agent name (default: healthcare-voice-ai)
```

**Optional:**
```bash
PORT                     # Server port (default 8000)
PIPECAT_TIMEOUT_SECONDS  # Bot start timeout (default 90)
```

### Bot Environment Variables (bot.py)

**Required (All Modes):**
```bash
OPENAI_API_KEY           # OpenAI for main LLM
GROQ_API_KEY             # Groq for classifier LLM
DEEPGRAM_API_KEY         # Deepgram for STT
ELEVENLABS_API_KEY       # ElevenLabs for TTS
DAILY_API_KEY            # Daily.co for telephony
DAILY_PHONE_NUMBER_ID    # Daily.co outbound phone number ID
MONGO_URI                # MongoDB connection string
```

**Optional:**
```bash
LANGFUSE_PUBLIC_KEY      # Langfuse observability
LANGFUSE_SECRET_KEY      # Langfuse observability
LANGFUSE_HOST            # Langfuse host URL
DEBUG                    # Enable debug mode (true/false)
ENABLE_TRACING           # Enable OpenTelemetry tracing (true/false)
OTEL_CONSOLE_EXPORT      # Print OpenTelemetry spans (true/false)
```

**Deployment Notes:**
- **Local Development**: All variables in `.env` file
- **Production Backend**: Variables in Fly.io secrets (set `ENV=production`)
- **Production Bot**: Variables in Pipecat Cloud secret set `healthcare-secrets` (see `pcc-deploy.toml`)

## Testing Strategy

Tests use pytest with async support (`pytest-asyncio`). Test files should follow naming convention `test_*.py`.

When testing pipeline components, mock Daily.co rooms and MongoDB connections to avoid external dependencies.

## Code Modification Guidelines

**When modifying conversation logic:**
- Edit the flow class in `clients/<client_name>/flow_definition.py`
- Update node definitions (prompts, functions, transitions)
- Modify function handlers for business logic changes
- Service settings go in `services.yaml`

**When adding new conversation nodes:**
1. Create `create_<node_name>_node()` method in flow class
2. Define `role_messages` (persona) and `task_messages` (instructions)
3. Add function handlers for transitions
4. Update existing nodes to transition to new node

**When adding new clients:**
1. Create `clients/<client_name>/` directory
2. Create `flow_definition.py` with `<ClientName>Flow` class
3. Implement node factory methods and function handlers
4. Copy and configure `services.yaml`

**When modifying core pipeline:**
- Changes to `pipeline/` affect all clients - test thoroughly
- FlowManager integration is in `pipeline_factory.py` and `runner.py`
- IVR event handlers are in `handlers/ivr.py`

**When adding new LLM functions:**
- Define function in flow class (e.g., `async def my_function(self, flow_manager, ...)`)
- Add to node's `functions` array
- Return `(result, next_node)` tuple
- Result is provided to LLM for context
- next_node transitions flow (or None to stay in current node)
