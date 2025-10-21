# 10.13.2025
- Add a single patient (add patients in bulk), complete a full call, go through all states in schema.yaml, input prior auth status and reference number into a database
- Website must correctly show call statuses dynamically as they occur - Start Call, Call In Progress, Call Completed
- Newly added patient must show in db, once call completed, Patient row stays in the list with Status = "Call Completed"
- No .js basic popups and confirm messages showed in any step on the frontend (all confirm messages deleted)
- Voice Agent is able to close the call (or realize the human hangs up) and terminate the pipeline after verifying that we have all info needed

# 10.14.2025 - 10.16.2025
- Voicemail detection
- IVR navigation

# 10.17.2025
- Robust pipeline termination
  - Once caller hangs up, terminate
  - Once Closing state, terminate
- Transition is automatic from greeting to verification
- Transition is llm based from verification to closing (llm decides we have all done, status and reference number inserted --> close call)

# 10.18.2025
- Today's topic is observability, monitoring, and evaluations of the main coversation flow

Goals:
1. Imrpove the attached latency monitoring to pinpoint latency issues (setup thresholds, add colors, format numbers)
2. Implement post-call full transcription, including prompts passed, responses generated, user messages into terminal, simply the whole conversation with everything spoken / passed to llm
  - If simple, prepare to push this whole monitoring into frontend (React, Vite, shadcn/ui), so that every call can be reviewed after completion with time stamps, prompts, responses
Details about my implementation are below:
1. I use pipecat, OpenAI, Daily for telephony, Elevenlabs, deepgram, i have multiple states per the conversation with custom prompts being passed into the llm, then there are function calls to update the db. 
2. I have a monitoring setup that I'd like improved. See how it works attached:

# 10.19.2025
- Provide full transcipt after a call

# 10.20.2025
- Encrypt data in transit and in storage

Implement cost per minute of call tracking
Fix call status visibility, Patient Details - Back to list button
Start mulitple calls at the same time
Add sign in / log in buttons with mfa
Change theme, include navigation menu component

https://ui.shadcn.com/blocks/signup
https://ui.shadcn.com/themes
https://ui.shadcn.com/docs/components/menubar
https://ui.shadcn.com/docs/components/empty
https://ui.shadcn.com/docs/components/sheet
https://ui.shadcn.com/docs/components/pagination#
https://ui.shadcn.com/docs/components/data-table


# Application Flow:
1. app.py launches FastAPI server
2. Loads environment variables (.env)
3. Initializes database connection (backend/models.py) - for now, simple solution, might need updates in the future
4. Exposes REST endpoints (/start_call, /get_state, etc.)
5. Ready to receive call requests ✓
**At this point, NO pipelines are created yet.** The system just waits for requests.
### 📞 How a New Call Starts

User clicks "Start Call" in frontend
    ↓
POST /start_call {patient_id: "123", client_name: "prior_auth"}
    ↓
app.py receives request
    ↓
┌─────────────────────────────────────────────────────────────┐
│ STEP 1: Load Client Configuration                          │
│ core/client_loader.py → Reads clients/prior_auth/*.yaml    │
│   - schema.yaml (conversation flow rules)                  │
│   - prompts.yaml (what AI says at each state)              │
│   - services.yaml (which STT/TTS/LLM to use)               │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│ STEP 2: Create Pipeline Runner                             │
│ pipeline/runner.py → ConversationPipeline instance          │
│   - Stores client config                                   │
│   - Stores session data (patient info, phone number)       │
│   - NOT running yet, just prepared                         │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│ STEP 3: Build Pipeline (when .run() called)                │
│ pipeline/pipeline_factory.py → Assembles components:       │
│                                                             │
│ A. Instantiate Services (services/service_factory.py)      │
│    - Deepgram STT (speech recognition)                     │
│    - ElevenLabs TTS (voice synthesis)                      │
│    - OpenAI LLM (conversation AI)                          │
│    - Daily.co Transport (telephony)                        │
│                                                             │
│ B. Create Conversation Components                          │
│    - ConversationContext (tracks current state)            │
│    - StateManager (handles transitions)                    │
│    - PromptRenderer (fills in templates)                   │
│                                                             │
│ C. Create Event Handlers (handlers/*.py)                   │
│    - TranscriptHandler → logs conversation                 │
│    - VoicemailHandler → detects/handles voicemail          │
│    - IVRHandler → navigates phone menus                    │
│    - TransportHandler → manages dial-out events            │
│                                                             │
│ D. Wire Everything Into Pipeline                           │
│    Transport → Audio → STT → Voicemail → IVR → LLM →      │
│    TTS → Audio → Transport                                 │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│ STEP 4: Execute Call                                        │
│ pipeline/runner.py → Runs PipelineTask                      │
│   1. Bot joins Daily.co room                               │
│   2. Dials patient phone number                            │
│   3. Listens for answer/voicemail/IVR                      │
│   4. Conducts conversation based on schema                 │
│   5. Transitions through states (greeting → verification)  │
│   6. Calls functions when needed (update_prior_auth)       │
│   7. Ends gracefully when done                             │
└─────────────────────────────────────────────────────────────┘
    ↓
Call Complete → Pipeline destroyed

# 2. Add 3 YAML files
clients/appointment_reminder/
├── schema.yaml      # Different conversation flow
├── prompts.yaml     # Different prompts/personality  
└── services.yaml    # Maybe use different voice/model

**The system:**
1. Loads the correct client's YAMLs from `clients/{client_name}/`
2. Builds a pipeline with that client's configuration
3. Runs a call using that client's conversation flow

┌─────────────────────────────────────────────────────────────┐
│                    VOICE AI PHONE CALL                      │
├─────────────────────────────────────────────────────────────┤
│ 1. Telephony          → Daily.co dials the number           │
│ 2. Audio Processing   → Converts phone audio to 16kHz mono  │
│ 3. Speech Recognition → Deepgram transcribes speech to text │
│ 4. Voicemail Detection→ Classifier LLM detects IVR          │
│ 5. IVR Navigation     → Navigates "Press 1 for..." menus    │
│ 6. Conversation State → Tracks states (greeting/closing)    │
│ 7. State Transitions  → Rules for moving between states     │
│ 8. LLM Generation     → OpenAI generates responses          │
│ 9. Prompt Management  → Loads the right prompt per state    │
│ 10. Function Calling  → Updates database during call        │
│ 11. Voice Synthesis   → ElevenLabs speaks responses         │
│ 12. Transcript Logging→ Records what was said               │
└─────────────────────────────────────────────────────────────┘
