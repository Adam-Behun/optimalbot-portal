● COMPREHENSIVE CODEBASE REFACTORING PROMPT

  Executive Summary

  Refactor the MyRobot healthcare voice AI codebase to establish a single, production-grade architecture using Pipecat Cloud
  exclusively. Eliminate all dual code paths, backward compatibility layers, and non-cloud implementations while maintaining
  HIPAA compliance and enabling rapid development iteration.

  ---
  I. CURRENT STATE ANALYSIS

  A. Identify and Document All Call Initialization Patterns

  Task: Scan the codebase and create an inventory of:

  1. All endpoints/functions that start calls:
    - Search for: POST /start-call, start_call, initiate_call, Daily.co room creation
    - Document: File location, parameters, dependencies
  2. All bot execution methods:
    - Local pipeline execution (search for: SchemaBasedPipeline, ConversationPipeline, direct Pipecat pipeline instantiation)
    - Pipecat Cloud API calls (search for: pipecat, agent start, REST API calls to Pipecat Cloud)
    - Document: Which code path is production, which is legacy
  3. Configuration loading patterns:
    - How clients/prior_auth/*.yaml files are loaded
    - Environment variable usage patterns
    - Service instantiation (STT, TTS, LLM, Daily transport)
  4. Entry points:
    - app.py - FastAPI backend
    - bot.py - Pipecat Cloud agent
    - Any test scripts or alternative runners

  B. Identify Redundant Code

  Files to examine:
  app.py                          # FastAPI backend - likely contains dual paths
  bot.py                          # Pipecat Cloud agent entry point
  schema_pipeline.py              # May be legacy local pipeline
  pipeline/runner.py              # Pipeline orchestration
  pipeline/pipeline_factory.py    # Pipeline assembly
  services/service_factory.py     # Service creation

  Questions to answer:
  1. Does app.py contain in-process bot execution code AND Pipecat Cloud API calls?
  2. Is schema_pipeline.py used by both local and cloud paths?
  3. Are there conditional imports based on environment (e.g., if PIPECAT_CLOUD_ENABLED:)?
  4. Are service factories creating services that are only used locally vs. only in cloud?

  C. HIPAA Compliance Audit

  Identify all locations where PHI is:
  1. Logged: Search for all logger.debug(), print(), console.log() statements
  2. Transmitted: API endpoints, MongoDB queries, external service calls
  3. Stored: MongoDB schema, temporary files, cached data
  4. Exposed: Error messages, stack traces, observability traces

  Critical files to audit:
  backend/models.py              # MongoDB patient schema
  handlers/transcript.py         # Call transcript handling
  app.py                         # API endpoints exposing patient data
  backend/functions.py           # LLM functions updating patient records

  Document:
  - Any PHI logged to stdout/stderr
  - Any PHI sent to Langfuse/OpenTelemetry without encryption
  - Any PHI in error responses
  - Any temporary file storage of patient data

  ---
  II. TARGET ARCHITECTURE SPECIFICATION

  A. Single Source of Truth: Pipecat Cloud-Only Architecture

  Architecture Diagram:
  ┌─────────────────────────────────────────────────────────┐
  │ PRODUCTION ARCHITECTURE (SINGLE PATH)                    │
  ├─────────────────────────────────────────────────────────┤
  │                                                          │
  │  [User Browser]                                          │
  │        ↓                                                 │
  │  [Vercel Frontend] ─────→ [Fly.io FastAPI Backend]     │
  │                                  ↓                       │
  │                            POST /start-call             │
  │                                  ↓                       │
  │              Pipecat Cloud REST API (ONLY PATH)         │
  │                                  ↓                       │
  │         [Pipecat Cloud Agent: healthcare-voice-ai]      │
  │            (Runs bot.py with schema-driven logic)       │
  │                                  ↓                       │
  │              [Daily.co] ← ───── ┘                       │
  │                                                          │
  │  Data Flow:                                              │
  │  Backend → Fetch patient from MongoDB                   │
  │  Backend → Call Pipecat Cloud API with patient data     │
  │  Pipecat Cloud → Execute bot.py                         │
  │  bot.py → Connect to Daily.co room                      │
  │  bot.py → Make phone call via Daily                     │
  │  bot.py → Update MongoDB via LLM function calls         │
  │  bot.py → Send traces to Langfuse                       │
  └─────────────────────────────────────────────────────────┘

  Eliminated:
  - ❌ Local pipeline execution in app.py
  - ❌ Direct Daily.co room creation from backend
  - ❌ In-process bot instantiation
  - ❌ Conditional logic: if use_pipecat_cloud else run_local

  Retained:
  - ✅ bot.py - Pipecat Cloud agent (ONLY place bot logic runs)
  - ✅ app.py - FastAPI backend (ONLY calls Pipecat Cloud API)
  - ✅ Schema-driven YAML configs in clients/
  - ✅ Shared modules: core/, pipeline/, handlers/, services/ (used by bot.py only)

  B. Clean Separation: Backend vs. Bot

  FastAPI Backend (app.py) - Responsibilities:
  # ONLY these responsibilities:
  1. CRUD operations on patients (MongoDB)
  2. Authentication/authorization (JWT)
  3. REST API for frontend
  4. Call Pipecat Cloud API to start agent sessions
  5. Webhook handler for agent callbacks (if needed)
  6. Health checks

  Pipecat Cloud Agent (bot.py) - Responsibilities:
  # ONLY these responsibilities:
  1. Receive session start request from Pipecat Cloud
  2. Load patient data from arguments
  3. Initialize schema-driven pipeline (YAML configs)
  4. Execute conversation (STT/TTS/LLM)
  5. Navigate IVR, conduct call
  6. Update patient records via LLM functions
  7. Send observability traces to Langfuse

  Shared Code (core/, pipeline/, handlers/, services/):
  - Used ONLY by bot.py
  - NOT imported by app.py
  - Backend only needs: backend/models.py for MongoDB access

  C. Call Initialization Flow (Single Path)

  Refactored /start-call endpoint:
  # app.py - SIMPLIFIED (pseudocode to guide implementation)

  @app.post("/start-call")
  async def start_call(patient_id: str):
      # 1. Fetch patient from MongoDB
      patient = await get_patient(patient_id)

      # 2. Build agent arguments (patient data, phone number)
      agent_args = {
          "patient_id": patient_id,
          "phone_number": patient.phone_number,
          "patient_data": patient.to_dict()  # Pass data to agent
      }

      # 3. Call Pipecat Cloud API (ONLY WAY TO START CALL)
      response = await pipecat_cloud_client.start_agent(
          agent_name="healthcare-voice-ai",
          arguments=agent_args
      )

      # 4. Return session info to frontend
      return {"session_id": response.session_id, "status": "started"}

  Eliminated from app.py:
  - ❌ SchemaBasedPipeline instantiation
  - ❌ Daily.co room creation
  - ❌ Pipeline runner execution
  - ❌ Service factory calls (STT/TTS/LLM)
  - ❌ Any async def run_bot() functions

  D. Bot Entry Point (bot.py)

  Standardized structure:
  # bot.py - STRUCTURE TO FOLLOW

  from pipecat.transports.services.daily import DailyParams
  # ... other imports

  async def bot(args: DailyRunnerArguments):
      """
      Entry point called by Pipecat Cloud.
      This is the ONLY place bot logic executes.
      """

      # 1. Extract patient data from args
      patient_id = args.arguments.get("patient_id")
      phone_number = args.arguments.get("phone_number")
      patient_data = args.arguments.get("patient_data")

      # 2. Load schema-driven configuration
      client_config = ClientLoader.load("prior_auth")

      # 3. Build pipeline (services, processors, handlers)
      pipeline = PipelineFactory.create(client_config, patient_data)

      # 4. Setup observability (Langfuse)
      setup_tracing(session_id=args.session_id)

      # 5. Connect to Daily.co and place outbound call
      transport = DailyTransport(...)
      await transport.dial_out(phone_number)

      # 6. Run pipeline until call completes
      await pipeline.run()

      # 7. Cleanup and return

  # Required by Pipecat Cloud base image
  if __name__ == "__main__":
      # Base image handles calling bot() function
      pass

  Key requirements:
  - MUST have async def bot(args: DailyRunnerArguments) signature
  - Receives ALL data via args.arguments (no database access from bot)
  - Self-contained: loads config, runs call, exits
  - No HTTP server in bot.py (Pipecat Cloud manages lifecycle)

  ---
  III. REFACTORING EXECUTION PLAN

  Phase 1: Analysis and Documentation (Non-Breaking)

  Step 1.1: Create Inventory
  - List all files that instantiate pipelines
  - List all files that import SchemaBasedPipeline, ConversationPipeline, PipelineFactory
  - List all HTTP endpoints that start calls or create Daily.co rooms
  - Document current environment variables and their usage
  - Map which code runs locally vs. in Pipecat Cloud

  Step 1.2: HIPAA Audit
  - Search codebase for logger.debug(.*patient), print(.*patient), console.log(.*patient)
  - Review all MongoDB queries for proper field projection (don't over-fetch PHI)
  - Check Langfuse trace configuration - ensure PHI fields are redacted
  - Verify CORS, JWT, and API authentication are properly configured
  - Document all external API calls (OpenAI, Deepgram, ElevenLabs) - confirm they're HIPAA BAAs signed

  Step 1.3: Test Current Production
  - Make a test call using current production deployment
  - Verify which code path is actually used (Pipecat Cloud or local?)
  - Document current behavior as baseline

  Deliverable: Detailed inventory document listing all findings.

  ---
  Phase 2: Backend Simplification (app.py)

  Step 2.1: Remove Local Pipeline Code

  Files to modify:
  app.py                    # Main refactor target

  Remove from app.py:
  1. All imports of:
    - SchemaBasedPipeline
    - ConversationPipeline
    - PipelineFactory
    - ServiceFactory
    - core/, pipeline/, handlers/ modules (except backend/)
  2. All functions/endpoints that:
    - Create pipelines locally
    - Instantiate Daily.co rooms directly
    - Run bot logic in-process
  3. Environment variables no longer needed by backend:
    - OPENAI_API_KEY (only bot needs this)
    - DEEPGRAM_API_KEY (only bot needs this)
    - ELEVENLABS_API_KEY (only bot needs this)
    - Keep: PIPECAT_API_KEY, MONGO_URI, JWT_SECRET_KEY, ALLOWED_ORIGINS

  Add to app.py:
  1. Pipecat Cloud SDK/API client:
  # New dependency to add
  from pipecatcloud import PipecatCloudClient

  pipecat_client = PipecatCloudClient(
      api_key=os.getenv("PIPECAT_API_KEY"),
      agent_name=os.getenv("PIPECAT_AGENT_NAME", "healthcare-voice-ai")
  )
  2. Simplified /start-call endpoint:
    - Fetch patient from MongoDB
    - Call pipecat_client.start_agent() with patient data
    - Return session info
  3. Optional: /agent-webhook endpoint for callbacks from Pipecat Cloud

  Step 2.2: Update Dockerfile.api

  Verify Dockerfile.api ONLY includes:
  COPY app.py .
  COPY backend/ ./backend/        # MongoDB models only
  COPY utils/ ./utils/            # Utility functions
  # DO NOT COPY: core/, pipeline/, handlers/, services/, clients/, bot.py

  If currently copying unnecessary directories, remove them.

  Step 2.3: Update Fly.io Environment Variables

  Remove from Fly.io secrets (backend doesn't need these):
  fly secrets unset OPENAI_API_KEY DEEPGRAM_API_KEY ELEVENLABS_API_KEY DAILY_API_KEY DAILY_PHONE_NUMBER_ID

  Keep only:
  PIPECAT_API_KEY
  PIPECAT_AGENT_NAME
  MONGO_URI
  JWT_SECRET_KEY
  ALLOWED_ORIGINS
  LANGFUSE_PUBLIC_KEY (if backend has its own traces)
  LANGFUSE_SECRET_KEY
  LANGFUSE_HOST

  Step 2.4: Test Backend in Isolation
  - Deploy refactored backend to Fly.io
  - Test /health endpoint
  - Test patient CRUD operations
  - Verify /start-call returns proper response (without actually calling Pipecat Cloud yet)

  Deliverable: Simplified app.py with ONLY API and database logic.

  ---
  Phase 3: Bot Standardization (bot.py)

  Step 3.1: Review bot.py Structure

  Ensure bot.py follows Pipecat Cloud best practices:
  1. Entry point: async def bot(args: DailyRunnerArguments)
  2. Receives patient data from args.arguments (NOT from MongoDB)
  3. Uses schema-driven configuration from clients/prior_auth/
  4. Self-contained execution (no external dependencies except secrets from Pipecat Cloud secret set)

  Step 3.2: Patient Data Passing

  Problem: Bot shouldn't query MongoDB directly (separation of concerns).

  Solution: Backend passes patient data in agent start request:
  # app.py
  agent_args = {
      "patient_id": str(patient_id),
      "patient_name": patient.patient_name,
      "date_of_birth": patient.date_of_birth,
      "insurance_member_id": patient.insurance_member_id,
      "cpt_code": patient.cpt_code,
      # ... all fields needed for call
      "phone_number": patient.phone_number,
      "insurance_company_phone": "1-800-XXX-XXXX"
  }

  # bot.py
  async def bot(args: DailyRunnerArguments):
      patient_data = args.arguments  # All data from backend
      # Use patient_data directly, no MongoDB query

  Exception: LLM function calls CAN update MongoDB (e.g., update_prior_auth_status) - keep backend/functions.py and
  backend/models.py in bot deployment.

  Step 3.3: Verify Dockerfile.bot

  Current structure (verify this is optimal):
  FROM dailyco/pipecat-base:latest

  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt

  COPY bot.py .
  COPY core/ ./core/
  COPY pipeline/ ./pipeline/
  COPY handlers/ ./handlers/
  COPY services/ ./services/
  COPY clients/ ./clients/
  COPY backend/functions.py ./backend/functions.py   # For LLM function calls
  COPY backend/models.py ./backend/models.py         # For MongoDB updates
  COPY backend/sessions.py ./backend/sessions.py     # If needed

  # Base image provides CMD

  Optimization opportunity:
  - If backend/sessions.py is not needed by bot, remove it
  - Ensure .dockerignore excludes app.py, frontend/, tests/

  Step 3.4: Test Bot in Isolation

  Using Pipecat Cloud CLI:
  # Rebuild and push bot image
  docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .

  # Redeploy to Pipecat Cloud
  pipecatcloud deploy

  # Start a test session (manual trigger)
  pipecatcloud agent start healthcare-voice-ai --arguments '{"patient_id":"test","phone_number":"1234567890",...}'

  # Check logs
  pipecatcloud agent logs healthcare-voice-ai

  - Verify bot receives arguments correctly
  - Verify bot loads schema from clients/prior_auth/
  - Verify bot places call
  - Verify bot can update MongoDB via LLM functions

  Deliverable: Validated bot.py that runs exclusively on Pipecat Cloud.

  ---
  Phase 4: End-to-End Integration

  Step 4.1: Wire Backend to Pipecat Cloud

  In app.py, implement Pipecat Cloud client:

  Research needed:
  - Check Pipecat Cloud documentation for SDK/REST API
  - Determine if pipecatcloud Python package has programmatic API (not just CLI)
  - If no SDK, implement REST API calls manually:

  # Pseudocode - adjust based on actual Pipecat Cloud API
  import httpx

  async def start_pipecat_agent(patient_data: dict):
      async with httpx.AsyncClient() as client:
          response = await client.post(
              "https://api.pipecat.daily.co/v1/agents/healthcare-voice-ai/start",
              headers={"Authorization": f"Bearer {PIPECAT_API_KEY}"},
              json={"arguments": patient_data}
          )
          return response.json()

  Step 4.2: Update Frontend API Base URL

  Verify frontend is pointing to production backend:
  // frontend/.env.production
  VITE_API_BASE_URL=https://prior-auth-agent-v2.fly.dev

  Step 4.3: End-to-End Test

  Test flow:
  1. User opens frontend: https://callit-nothknhil-adambehun22-4968s-projects.vercel.app
  2. User creates patient record
  3. User clicks "Start Call"
  4. Frontend → Fly.io backend /start-call
  5. Backend → Pipecat Cloud API
  6. Pipecat Cloud → Starts agent session (bot.py)
  7. Bot → Places call via Daily.co
  8. Bot → Conducts conversation
  9. Bot → Updates MongoDB
  10. Frontend → Shows call status

  Validation:
  - Call completes successfully
  - Patient record updated with transcript
  - Langfuse shows traces
  - No errors in any logs

  Deliverable: Fully integrated production system with single code path.

  ---
  Phase 5: Development Workflow Optimization

  Step 5.1: Local Development Configuration

  Problem: Developers need fast iteration without deploying to Pipecat Cloud every change.

  Solution: Create dev_bot.py for local testing (SEPARATE from production bot.py):

  # dev_bot.py - LOCAL TESTING ONLY (not deployed)
  """
  Development-only script for testing bot logic locally.
  Does NOT replace bot.py - this is for rapid prompt iteration.
  """

  async def test_bot_locally():
      # Mock DailyRunnerArguments
      mock_args = MockArgs(
          arguments={"patient_id": "test", ...}
      )

      # Call same bot() function from bot.py
      from bot import bot
      await bot(mock_args)

  if __name__ == "__main__":
      asyncio.run(test_bot_locally())

  Usage:
  # Fast iteration on prompts/logic
  python dev_bot.py  # Test locally without deployment

  # When ready, deploy to Pipecat Cloud
  docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .
  pipecatcloud deploy

  Step 5.2: Environment Variable Management

  Create .env.example with documented variables:
  # .env.example - Template for local development

  # ============================================
  # LOCAL DEVELOPMENT ENVIRONMENT VARIABLES
  # ============================================

  # Pipecat Cloud (REQUIRED for local backend)
  PIPECAT_API_KEY=pk_xxx        # From: pipecatcloud organizations keys create
  PIPECAT_AGENT_NAME=healthcare-voice-ai

  # MongoDB (REQUIRED)
  MONGO_URI=mongodb+srv://...   # MongoDB Atlas connection string

  # Authentication (REQUIRED)
  JWT_SECRET_KEY=xxx            # Generate: openssl rand -base64 32

  # CORS (REQUIRED for local frontend)
  ALLOWED_ORIGINS=http://localhost:3000

  # Observability (OPTIONAL)
  LANGFUSE_PUBLIC_KEY=pk-lf-xxx
  LANGFUSE_SECRET_KEY=sk-lf-xxx
  LANGFUSE_HOST=https://us.cloud.langfuse.com

  # ============================================
  # BOT-ONLY VARIABLES (Not needed for backend)
  # Only used by bot.py when testing dev_bot.py
  # ============================================
  OPENAI_API_KEY=sk-xxx
  DEEPGRAM_API_KEY=xxx
  ELEVENLABS_API_KEY=sk_xxx
  DAILY_API_KEY=xxx
  DAILY_PHONE_NUMBER_ID=xxx

  Step 5.3: Update CLAUDE.md

  Rewrite development commands section:
  ## Development Workflow

  ### Backend Development (FastAPI)
  ```bash
  # 1. Activate environment
  source venv/bin/activate

  # 2. Run backend locally
  python app.py  # Runs on http://localhost:8000

  # 3. Make changes to app.py, backend/*, utils/*
  # Hot reload enabled - changes reflect immediately

  # 4. Deploy when ready
  fly deploy

  Frontend Development (React)

  # 1. Navigate to frontend
  cd frontend

  # 2. Run dev server
  npm run dev  # Runs on http://localhost:3000

  # 3. Make changes - hot reload enabled

  # 4. Deploy when ready
  vercel --prod

  Bot Development (Voice Agent)

  # METHOD 1: Quick iteration (local testing)
  python dev_bot.py  # Test bot logic without deployment

  # METHOD 2: Full deployment (production testing)
  # 1. Make changes to bot.py, core/*, pipeline/*, handlers/*, clients/*
  # 2. Rebuild image
  docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .

  # 3. Deploy to Pipecat Cloud
  pipecatcloud deploy

  # 4. Test
  pipecatcloud agent start healthcare-voice-ai --arguments '{"patient_id":"test",...}'

  # 5. Check logs
  pipecatcloud agent logs healthcare-voice-ai

  Schema/Prompt Changes (YAML configs)

  # Edit YAML files
  vim clients/prior_auth/prompts.yaml
  vim clients/prior_auth/schema.yaml

  # Rebuild bot (YAML is baked into image)
  docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .
  pipecatcloud deploy

  # OR use dev_bot.py for faster iteration
  python dev_bot.py  # Uses local YAML files

  **Deliverable:** Optimized development workflow documentation.

  ---

  ### Phase 6: HIPAA Compliance Hardening

  **Step 6.1: PHI Logging Elimination**

  **Search and destroy all PHI logging:**
  ```bash
  # Find potential PHI leaks
  grep -r "logger.debug.*patient" .
  grep -r "print.*patient" .
  grep -r "console.log.*patient" frontend/

  Remediation:
  # BAD - Logs entire patient object (PHI)
  logger.debug(f"Starting call for patient: {patient}")

  # GOOD - Logs only non-PHI identifier
  logger.debug(f"Starting call for patient_id: {patient.id}")

  Apply to all files:
  - app.py
  - bot.py
  - backend/models.py
  - backend/functions.py
  - handlers/transcript.py
  - All core/, pipeline/, handlers/ files

  Step 6.2: Langfuse Trace Sanitization

  Review OpenTelemetry configuration:
  # Ensure PHI is NOT sent to Langfuse
  # Check: pipeline/runner.py, any setup_tracing() calls

  # Add span processor to redact PHI fields
  from opentelemetry.sdk.trace.export import SpanExportProcessor

  class PHIRedactingProcessor(SpanExportProcessor):
      REDACT_FIELDS = ["patient_name", "date_of_birth", "insurance_member_id", "phone_number"]

      def on_end(self, span):
          for attr in self.REDACT_FIELDS:
              if attr in span.attributes:
                  span.attributes[attr] = "[REDACTED]"
          super().on_end(span)

  Step 6.3: API Response Sanitization

  Review all API endpoints:
  # app.py - Ensure error responses don't leak PHI

  @app.exception_handler(Exception)
  async def global_exception_handler(request, exc):
      # BAD - May leak PHI in error message
      return JSONResponse({"error": str(exc)})

      # GOOD - Generic error, log details server-side only
      logger.error(f"Error processing request: {exc}", exc_info=True)
      return JSONResponse({"error": "An error occurred"}, status_code=500)

  Step 6.4: MongoDB Security

  Verify MongoDB connection:
  - Uses TLS/SSL (ssl=true in connection string)
  - IP whitelist configured (only Fly.io IPs + Pipecat Cloud IPs)
  - Strong authentication (not default credentials)
  - Database-level encryption at rest enabled (MongoDB Atlas config)

  Step 6.5: Secrets Management Audit

  Verify no secrets in code:
  # Search for hardcoded secrets
  grep -r "sk-" .  # OpenAI keys
  grep -r "pk-" .  # Pipecat/Langfuse keys
  grep -r "mongodb://" .  # Hardcoded DB URIs

  All secrets must be:
  - In .env (local, gitignored)
  - In Pipecat Cloud secret set (bot deployment)
  - In Fly.io secrets (backend deployment)
  - In Vercel environment variables (frontend)

  Step 6.6: Create HIPAA Compliance Checklist

  Document as HIPAA_COMPLIANCE.md:
  # HIPAA Compliance Checklist

  ## Data Protection
  - [x] PHI encrypted in transit (HTTPS/TLS everywhere)
  - [x] PHI encrypted at rest (MongoDB Atlas encryption)
  - [x] No PHI in application logs
  - [x] No PHI in error messages
  - [x] No PHI in observability traces (Langfuse)

  ## Access Control
  - [x] JWT authentication on all API endpoints
  - [x] Role-based access control (if applicable)
  - [x] MongoDB access restricted by IP whitelist
  - [x] Secrets stored securely (not in code)

  ## Audit Trail
  - [x] All patient record access logged (non-PHI identifiers only)
  - [x] Call transcripts stored securely in MongoDB
  - [x] Langfuse traces available for debugging (PHI redacted)

  ## Third-Party Services (BAAs Required)
  - [x] MongoDB Atlas - BAA signed
  - [ ] OpenAI - BAA signed? (VERIFY)
  - [ ] Deepgram - BAA signed? (VERIFY)
  - [ ] ElevenLabs - BAA signed? (VERIFY)
  - [ ] Daily.co - BAA signed? (VERIFY)
  - [ ] Pipecat Cloud - BAA signed? (VERIFY)
  - [ ] Langfuse - BAA signed? (VERIFY)

  ## Incident Response
  - [ ] Data breach notification procedure documented
  - [ ] Access logs retention policy defined
  - [ ] Backup and disaster recovery plan in place

  Action items:
  - Contact each vendor to obtain signed BAAs
  - Document retention policies
  - Create incident response runbook

  Deliverable: Fully HIPAA-compliant codebase with audit trail.

  ---
  IV. ACCEPTANCE CRITERIA

  A. Code Quality

  The refactored codebase MUST:

  1. Single Code Path:
    - app.py contains ZERO pipeline instantiation code
    - app.py contains ZERO Daily.co room creation code
    - app.py ONLY calls Pipecat Cloud API to start agents
    - bot.py is the ONLY place bot logic executes
    - No conditional logic: if LOCAL_MODE else CLOUD_MODE
  2. Clean Separation:
    - Backend (app.py) imports ONLY: backend/models.py, backend/ utilities
    - Backend does NOT import: core/, pipeline/, handlers/, services/, SchemaBasedPipeline
    - Bot (bot.py) imports: core/, pipeline/, handlers/, services/, backend/models.py (for LLM functions)
    - Bot does NOT import: app.py, FastAPI, HTTP server code
  3. Minimal Dependencies:
    - Dockerfile.api ONLY copies: app.py, backend/, utils/
    - Dockerfile.bot ONLY copies: bot.py, core/, pipeline/, handlers/, services/, clients/, backend/models.py,
  backend/functions.py
    - requirements.txt split into requirements-backend.txt and requirements-bot.txt (if beneficial)
  4. Configuration Management:
    - .env.example documents all required variables
    - Fly.io secrets contain ONLY backend-required vars
    - Pipecat Cloud secret set contains ONLY bot-required vars
    - No environment variable is duplicated unnecessarily

  B. Functionality

  The system MUST:

  1. End-to-End Call Flow:
    - User can create patient via frontend
    - User can initiate call via frontend
    - Backend receives request, calls Pipecat Cloud API
    - Pipecat Cloud starts agent (bot.py)
    - Bot places outbound call
    - Bot conducts conversation using schema
    - Bot updates patient record via LLM function
    - Bot saves transcript to MongoDB
    - Frontend shows call completion status
  2. Local Development:
    - python app.py runs backend locally on :8000
    - npm run dev runs frontend locally on :3000
    - python dev_bot.py tests bot logic locally (optional dev tool)
    - Local backend can call Pipecat Cloud API (same as production)
  3. Production Deployment:
    - fly deploy deploys backend to Fly.io
    - vercel --prod deploys frontend to Vercel
    - docker buildx build + pipecatcloud deploy deploys bot to Pipecat Cloud
    - All three services communicate correctly

  C. HIPAA Compliance

  The system MUST:

  1. PHI Protection:
    - Zero PHI in application logs (verified via grep)
    - Zero PHI in error responses (verified via API testing)
    - Zero PHI in Langfuse traces (verified via dashboard inspection)
    - All PHI encrypted in transit (HTTPS/TLS verified)
    - All PHI encrypted at rest (MongoDB config verified)
  2. Access Control:
    - All API endpoints require authentication (JWT)
    - MongoDB accessible only from authorized IPs
    - Pipecat Cloud secrets properly scoped (not exposed to backend)
  3. Audit Trail:
    - HIPAA_COMPLIANCE.md created and up-to-date
    - All BAAs documented or obtained
    - Incident response procedure documented

  D. Documentation

  Must be updated:

  1. CLAUDE.md:
    - Reflects single-path architecture
    - Documents new development workflow
    - Removes outdated references to local pipeline
    - Clarifies backend vs. bot responsibilities
  2. README.md (if exists):
    - Updated deployment instructions
    - Updated environment variable requirements
    - Architecture diagram reflects new design
  3. Code Comments:
    - app.py has clear comments explaining Pipecat Cloud integration
    - bot.py has clear comments explaining entry point and data flow
    - Removed obsolete TODO comments referencing old architecture

  ---
  V. TESTING PROTOCOL

  Pre-Refactor Baseline

  Before any changes:
  1. Make a production test call - document success criteria
  2. Export current Langfuse trace as baseline
  3. Export patient record before/after call
  4. Take screenshots of frontend UI during call

  Post-Refactor Validation

  After each phase:
  1. Run same test call - compare to baseline
  2. Verify Langfuse trace matches expected flow
  3. Verify patient record updates identically
  4. Verify frontend UI behavior unchanged

  Regression Test Suite

  Create automated tests (if not exist):
  # tests/test_backend_api.py
  async def test_start_call_endpoint():
      # Verify /start-call returns proper response
      # Verify Pipecat Cloud API is called (mocked)
      pass

  # tests/test_bot_logic.py
  async def test_bot_schema_loading():
      # Verify bot loads YAML configs correctly
      pass

  async def test_bot_patient_data_handling():
      # Verify bot receives and uses patient data
      pass

  Run tests:
  pytest tests/ -v

  ---
  VI. ROLLBACK PLAN

  Git Strategy

  Before starting refactor:
  # Create refactor branch
  git checkout -b refactor/single-path-architecture

  # Tag current production state
  git tag pre-refactor-baseline
  git push origin pre-refactor-baseline

  Commit strategy:
  - Commit after each phase
  - Each commit message references phase number
  - Keep commits small and reversible

  ---
  VII. PRIORITIZATION AND RISK ASSESSMENT

  High Priority (Must Complete)

  1. Phase 2: Backend Simplification - High impact, medium risk
    - Eliminates dual code paths
    - Risk: Breaking production call flow
    - Mitigation: Test thoroughly in staging
  2. Phase 6: HIPAA Compliance - High impact, HIGH RISK
    - Critical for healthcare compliance
    - Risk: PHI exposure = legal liability
    - Mitigation: Automated PHI detection scans

  Medium Priority (Should Complete)

  3. Phase 3: Bot Standardization - Medium impact, low risk
    - Improves maintainability
    - Risk: Bot logic may break
    - Mitigation: Bot already on Pipecat Cloud, just cleanup
  4. Phase 5: Dev Workflow - Medium impact, low risk
    - Improves developer experience
    - Risk: None (doesn't affect production)

  Low Priority (Nice to Have)

  5. Phase 4: End-to-End Integration - Low impact (already working)
    - Validates everything works together
    - Risk: None (validation only)

  Recommended Order

  Week 1:
  - Phase 1: Analysis (2 days)
  - Phase 6: HIPAA Compliance (3 days) - DO THIS FIRST (legal risk)

  Week 2:
  - Phase 2: Backend Simplification (3 days)
  - Phase 3: Bot Standardization (2 days)

  Week 3:
  - Phase 4: Integration Testing (2 days)
  - Phase 5: Dev Workflow (2 days)
  - Documentation updates (1 day)

  ---
  VIII. SUCCESS METRICS

  Quantitative

  Code Metrics:
  - Lines of code reduced by >30% (removing duplication)
  - Cyclomatic complexity of /start-call endpoint < 5
  - Number of imports in app.py < 10

  Performance Metrics:
  - Call initiation time unchanged (± 5%)
  - Backend API response time < 200ms (for /start-call)
  - Bot cold start time < 10s (Pipecat Cloud metric)

  Reliability Metrics:
  - Call success rate ≥ 95% (same as baseline)
  - Zero PHI leaks in logs (100% compliance)
  - Zero production incidents during/after refactor

  Qualitative

  Developer Experience:
  - "Time to first call" for new developer < 30 minutes (setup + run)
  - Prompt iteration cycle < 5 minutes (edit YAML + test locally)
  - Production deployment cycle < 15 minutes (build + deploy + verify)

  Code Maintainability:
  - New team member can understand architecture in < 1 hour
  - Clear separation of concerns (backend vs. bot)
  - Single source of truth for configuration (no duplicate configs)

  ---
  IX. ADDITIONAL CONSIDERATIONS

  A. Future-Proofing

  Design decisions for scalability:

  1. Multi-Client Support:
    - Current: clients/prior_auth/
    - Future: clients/eligibility_verification/, clients/appointment_scheduling/
    - Ensure refactor supports multiple client configs without code changes
  2. Multi-Agent Support:
    - Current: Single agent healthcare-voice-ai
    - Future: Multiple agents for different use cases
    - Backend should parameterize agent name (already does via PIPECAT_AGENT_NAME)
  3. Webhook Support:
    - Pipecat Cloud may send webhooks (call completed, error occurred)
    - Add /pipecat-webhook endpoint to backend for async updates

  B. Observability Improvements

  Enhanced monitoring:

  1. Structured Logging:
  # Use structured logs for better querying
  logger.info("call_started", extra={
      "patient_id": patient_id,
      "session_id": session_id,
      "agent_name": agent_name
  })
  2. Custom Metrics:
    - Track call duration (Langfuse)
    - Track IVR navigation time
    - Track LLM token usage per call
    - Track cost per call
  3. Alerting:
    - Set up alerts for:
        - Call success rate drops below 90%
      - Average call duration > 10 minutes (may indicate stuck IVR)
      - PHI detected in logs (CRITICAL ALERT)

  C. Security Hardening

  Beyond HIPAA basics:

  1. Rate Limiting:
  # Add to app.py
  from slowapi import Limiter

  limiter = Limiter(key_func=get_remote_address)

  @app.post("/start-call")
  @limiter.limit("10/minute")  # Prevent abuse
  async def start_call(...):
      pass
  2. Input Validation:
    - Use Pydantic models for all request bodies
    - Sanitize patient data before passing to bot
    - Validate phone numbers (format, length)
  3. API Key Rotation:
    - Document procedure for rotating Pipecat Cloud API key
    - Document procedure for rotating MongoDB credentials
    - Set calendar reminders for quarterly rotation

  ---
  X. FINAL DELIVERABLES CHECKLIST

  Code:
  - Refactored app.py (backend only)
  - Refactored bot.py (standardized entry point)
  - Updated Dockerfile.api (minimal dependencies)
  - Updated Dockerfile.bot (verified optimal)
  - Created dev_bot.py (local testing tool)
  - Created .env.example (documented variables)
  - Removed obsolete files (e.g., schema_pipeline.py if unused)

  Documentation:
  - Updated CLAUDE.md (new architecture, workflows)
  - Created HIPAA_COMPLIANCE.md (compliance checklist)
  - Updated README.md (deployment instructions)
  - Created REFACTORING_SUMMARY.md (what changed, why, how to test)

  Configuration:
  - Updated Fly.io secrets (removed unnecessary vars)
  - Verified Pipecat Cloud secret set (complete)
  - Updated Vercel environment variables (if needed)
  - Updated pcc-deploy.toml (verified correct)
  - Updated fly.toml (verified correct)

  Testing:
  - End-to-end production test passed
  - Local development workflow tested
  - HIPAA PHI scan passed (zero PHI in logs)
  - Performance benchmarks met (call success rate ≥ baseline)

  Deployment:
  - Backend deployed to Fly.io (refactored version)
  - Bot deployed to Pipecat Cloud (standardized version)
  - Frontend deployed to Vercel (no changes needed, but redeployed for verification)
  - All services communicating correctly
  - Monitoring/alerting configured

  ---
  EXECUTION PROMPT FOR LLM

  You are now ready to execute this refactoring. When given this prompt, follow these instructions:

  1. Start with Phase 1: Analyze the current codebase according to Step 1.1-1.3. Output a detailed inventory before proceeding.
  2. For each phase:
    - Request user confirmation before proceeding to next phase
    - Output a summary of changes made
    - Highlight any deviations from plan
    - Flag any HIPAA compliance concerns immediately
  3. Use cautious approach:
    - Make one file change at a time
    - Show diffs before applying
    - Request review of critical changes (especially HIPAA-related)
    - Test after each major change
  4. Prioritize HIPAA compliance:
    - If ANY PHI exposure risk is detected, STOP and alert immediately
    - All logging changes must be reviewed manually
    - All API response changes must be reviewed manually
  5. Document as you go:
    - Keep running changelog of modifications
    - Note any assumptions made
    - Flag any missing information from original codebase

  BEGIN REFACTORING with Phase 1: Analysis and Documentation.