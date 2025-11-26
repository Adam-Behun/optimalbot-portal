# OpenAI Realtime S2S Implementation Plan

## Goals

- Add OpenAI Realtime speech-to-speech (S2S) pipeline for dial-in workflows
- Enable pure conversational interactions without IVR navigation, LLM switching, or complex flow state management
- Implement for `demo_clinic_beta/patient_questions` workflow as proof of concept
- Maintain transcript capture for MongoDB persistence and Langfuse observability
- Keep existing dial-out pipeline unchanged (no regression)
- Use WebSocket connection (server-side) with Daily.co telephony unchanged

## Architecture Overview

### Current Standard Pipeline (Dial-Out/Dial-In)
```
Caller → Daily (WebRTC/SIP) → Bot → Deepgram STT → OpenAI LLM → ElevenLabs TTS → Daily → Caller
```

### New S2S Pipeline (Dial-In Only)
```
Caller → Daily (WebRTC/SIP) → Bot → OpenAI Realtime (WebSocket) → Bot → Daily → Caller
```

**Key Difference:** OpenAI Realtime handles STT + LLM + TTS internally as a single service.

## Reference Documentation (CRITICAL)

**You MUST study these resources before implementing any phase:**

- **Pipecat OpenAI Realtime Service:**
  - API Reference: https://reference-server.pipecat.ai/en/latest/api/pipecat.services.openai.realtime.llm.html
  - Source Code: https://reference-server.pipecat.ai/en/latest/_modules/pipecat/services/openai/realtime/llm.html

- **OpenAI Realtime API Docs:** `/home/nope-4-0/projects/MyRobot/openai-realtime-s2s-example/Docs.md`

- **Existing Dial-In Implementation:** `/home/nope-4-0/projects/MyRobot/01_Dial-In-Implementation.md`

- **Current Pipeline Architecture:**
  - `pipeline/pipeline_factory.py` - How pipelines are built
  - `pipeline/runner.py` - ConversationPipeline class
  - `services/service_factory.py` - Service creation patterns

**Follow existing code patterns. Do not deviate from established architecture.**

## Key Technical Details

### OpenAIRealtimeLLMService

**Constructor Parameters:**
- `api_key` (str): OpenAI API key
- `model` (str): Default "gpt-realtime" (set at connection, cannot change mid-session)
- `base_url` (str): Default "wss://api.openai.com/v1/realtime"
- `session_properties` (SessionProperties): Session configuration
- `start_audio_paused` (bool): Initial audio input state (default False)

**SessionProperties Configuration:**
- `instructions` (str): System prompt
- `voice` (str): Voice ID (alloy, echo, fable, onyx, nova, shimmer)
- `audio` (AudioConfiguration): Input/output audio settings
  - `input.transcription` (InputAudioTranscription): Enable input transcription
  - `input.turn_detection` (SemanticTurnDetection | None): Turn detection settings

**Audio Rates:**
- Input: Accepts any sample rate (will be resampled)
- Output: Always 24kHz

**Transcript Events:**
- Input transcription: `TranscriptionFrame` (user speech → text)
- Output transcription: Embedded in response events

### Connection Method: WebSocket

OpenAI Realtime uses WebSocket for server-side applications (not WebRTC which is for browsers). The Pipecat `OpenAIRealtimeLLMService` handles the WebSocket connection internally.

---

## Phase 1: Service Factory Extension

### Claude Implementation Prompt

```
Implement Phase 1 of the S2S Implementation Plan.

TASK: Add create_s2s() method to ServiceFactory for creating OpenAI Realtime service.

FILES TO MODIFY:
- services/service_factory.py

REQUIREMENTS:
1. Add imports for OpenAI Realtime classes at top of file
2. Add create_s2s() static method after create_tts() method (~line 150)
3. Method should accept config dict and system_instruction string
4. Create SessionProperties with:
   - AudioConfiguration with InputAudioTranscription and SemanticTurnDetection
   - System instruction from parameter
   - Voice from config (default 'alloy')
5. Return OpenAIRealtimeLLMService instance

REFERENCE:
- Study existing create_stt(), create_llm(), create_tts() methods for patterns
- Use Pipecat imports from pipecat.services.openai.realtime

DO NOT modify any other methods or files in this phase.
```

### Phase 1 Goals

- Add method to create OpenAI Realtime S2S service
- Follow existing ServiceFactory patterns
- Configure session properties for conversational use

### Phase 1 Implementation

**File: `services/service_factory.py`**

Add imports at top:
```python
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
from pipecat.services.openai.realtime.events import (
    SessionProperties,
    AudioConfiguration,
    AudioInput,
    InputAudioTranscription,
    SemanticTurnDetection,
)
```

Add method after `create_tts()` (~line 150):
```python
@staticmethod
def create_s2s(config: Dict[str, Any], system_instruction: str) -> OpenAIRealtimeLLMService:
    """Create OpenAI Realtime S2S service.

    Args:
        config: S2S configuration from services.yaml
        system_instruction: System prompt for the conversation

    Returns:
        Configured OpenAIRealtimeLLMService instance
    """
    session_properties = SessionProperties(
        audio=AudioConfiguration(
            input=AudioInput(
                transcription=InputAudioTranscription(),
                turn_detection=SemanticTurnDetection(),
            )
        ),
        instructions=system_instruction,
        voice=config.get('voice_id', 'alloy'),
    )

    return OpenAIRealtimeLLMService(
        api_key=config['api_key'],
        model=config.get('model', 'gpt-4o-realtime-preview'),
        session_properties=session_properties,
        start_audio_paused=False,
    )
```

### Phase 1 Testing

1. Import test: `python -c "from services.service_factory import ServiceFactory"`
2. Verify no syntax errors
3. Check imports resolve correctly

---

## Phase 2: Pipeline Factory S2S Routing

### Claude Implementation Prompt

```
Implement Phase 2 of the S2S Implementation Plan.

TASK: Add S2S pipeline routing and builder method to PipelineFactory.

FILES TO MODIFY:
- pipeline/pipeline_factory.py

REQUIREMENTS:
1. Modify build() method to detect S2S configuration and route accordingly
2. Check for 's2s' key in services_config AND call_type == "dial-in"
3. If S2S detected, call new _build_s2s_pipeline() method
4. Otherwise, continue with existing standard pipeline code

5. Add _build_s2s_pipeline() static method that:
   - Loads flow class to get system instruction
   - Creates transport with dialin_settings
   - Creates S2S service via ServiceFactory.create_s2s()
   - Creates context aggregator using s2s_service.create_context_aggregator()
   - Assembles minimal pipeline: transport.input() → s2s_service → transport.output()
   - Returns pipeline, params, transport, components dict

6. Components dict must include:
   - 'context_aggregator': from s2s_service.create_context_aggregator()
   - 'ivr_navigator': None (not used in S2S)
   - 'flow': flow instance
   - 'main_llm': None
   - 'classifier_llm': None
   - 'llm_switcher': None
   - 'call_type': 'dial-in'
   - 's2s_service': the service instance
   - 'is_s2s': True

REFERENCE:
- Study existing build() and _assemble_pipeline() methods
- S2S uses context aggregator from s2s_service.create_context_aggregator()
- PipelineParams: audio_in_sample_rate=16000, audio_out_sample_rate=24000

DO NOT modify _assemble_pipeline() or other existing methods beyond the routing logic.
```

### Phase 2 Goals

- Route S2S-configured workflows to dedicated pipeline builder
- Build minimal S2S pipeline without FlowManager complexity
- Maintain interface compatibility with runner.py

### Phase 2 Implementation

**File: `pipeline/pipeline_factory.py`**

Modify `build()` method starting at line 27:
```python
@staticmethod
def build(
    client_name: str,
    session_data: Dict[str, Any],
    room_config: Dict[str, str],
    dialin_settings: Dict[str, str] = None
) -> tuple:
    organization_slug = session_data.get('organization_slug')
    services_config = PipelineFactory._load_services_config(organization_slug, client_name)

    call_type = services_config.get('call_type')
    if not call_type:
        raise ValueError(f"Missing required 'call_type' in services.yaml")

    # Check for S2S configuration
    s2s_config = services_config.get('s2s')
    if s2s_config and call_type == "dial-in":
        return PipelineFactory._build_s2s_pipeline(
            client_name, session_data, room_config, services_config, dialin_settings
        )

    # ... existing standard pipeline code continues unchanged ...
```

Add `_build_s2s_pipeline()` method after `_assemble_pipeline()` (~line 195):
```python
@staticmethod
def _build_s2s_pipeline(
    client_name: str,
    session_data: Dict[str, Any],
    room_config: Dict[str, str],
    services_config: Dict[str, Any],
    dialin_settings: Dict[str, str]
) -> tuple:
    """Build minimal S2S pipeline for dial-in conversations.

    This creates a streamlined pipeline using OpenAI Realtime for
    speech-to-speech without separate STT/LLM/TTS services.
    """
    from core.flow_loader import FlowLoader

    # Load flow to get system instruction
    organization_slug = session_data.get('organization_slug')
    flow_loader = FlowLoader(organization_slug, client_name)
    FlowClass = flow_loader.load_flow_class()

    flow = FlowClass(
        patient_data=session_data['patient_data'],
        flow_manager=None,
        main_llm=None,
        classifier_llm=None,
        organization_id=session_data.get('organization_id')
    )

    system_instruction = flow.get_system_instruction()

    # Create transport
    transport = ServiceFactory.create_transport(
        services_config['services']['transport'],
        room_config['room_url'],
        room_config['room_token'],
        room_config['room_name'],
        dialin_settings
    )

    # Create S2S service
    s2s_service = ServiceFactory.create_s2s(
        services_config['s2s'],
        system_instruction
    )

    # Create context aggregator from S2S service
    context = OpenAILLMContext()
    context_aggregator = s2s_service.create_context_aggregator(context)

    # Assemble minimal pipeline
    pipeline = Pipeline([
        transport.input(),
        context_aggregator.user(),
        s2s_service,
        transport.output(),
        context_aggregator.assistant(),
    ])

    params = PipelineParams(
        audio_in_sample_rate=16000,
        audio_out_sample_rate=24000,
        allow_interruptions=True,
        enable_metrics=True,
        enable_usage_metrics=True
    )

    components = {
        'context_aggregator': context_aggregator,
        'ivr_navigator': None,
        'flow': flow,
        'main_llm': None,
        'classifier_llm': None,
        'llm_switcher': None,
        'call_type': 'dial-in',
        's2s_service': s2s_service,
        'is_s2s': True,
    }

    return pipeline, params, transport, components
```

Add required imports at top of file:
```python
from pipecat.services.openai.llm import OpenAILLMContext
```

### Phase 2 Testing

1. Import test: `python -c "from pipeline.pipeline_factory import PipelineFactory"`
2. Verify routing logic with mock services_config containing 's2s' key
3. Check _build_s2s_pipeline returns correct component structure

---

## Phase 3: Runner S2S Adaptation

### Claude Implementation Prompt

```
Implement Phase 3 of the S2S Implementation Plan.

TASK: Modify ConversationPipeline.run() to handle S2S pipeline with minimal handlers.

FILES TO MODIFY:
- pipeline/runner.py

REQUIREMENTS:
1. In run() method, after PipelineFactory.build() call:
   - Check components.get('is_s2s', False)
   - If True, use S2S-specific setup (no FlowManager, no IVR handlers)
   - If False, continue with existing standard setup

2. For S2S pipeline:
   - Create PipelineTask with enable_tracing=True (no enable_turn_tracking - S2S handles this)
   - Add span attribute "pipeline.type": "s2s"
   - Call self._setup_s2s_handlers()
   - DO NOT setup FlowManager, IVR handlers, or transcript handlers

3. Add _setup_s2s_handlers() method that registers:
   - on_first_participant_joined: Log connection, no flow initialization needed
   - on_participant_left: Save transcript, log disconnection
   - on_client_disconnected: Save transcript if not already saved, cancel task

4. Add _save_s2s_transcript() async method:
   - Check self.transcript_saved flag to avoid duplicates
   - Import and call save_transcript_to_db from handlers.transcript
   - Set self.transcript_saved = True

5. For S2S, transcripts are captured via TranscriptionFrame events from S2S service
   - Register handler on s2s_service for transcript events
   - Append to self.transcripts list

REFERENCE:
- Study existing run() method structure
- Study setup_dialin_handlers() in handlers/transport.py for handler patterns
- S2S service emits TranscriptionFrame for user speech

DO NOT modify the standard (non-S2S) code path beyond the routing check.
```

### Phase 3 Goals

- Handle S2S pipeline in runner without FlowManager
- Setup minimal event handlers for connect/disconnect
- Capture transcripts from S2S service events

### Phase 3 Implementation

**File: `pipeline/runner.py`**

Modify `run()` method to add S2S branch after PipelineFactory.build():
```python
async def run(self, room_url: str, room_token: str, room_name: str):
    # ... existing session_data and room_config setup ...

    self.pipeline, params, self.transport, components = PipelineFactory.build(
        self.client_name,
        session_data,
        room_config,
        self.dialin_settings
    )

    self.flow = components['flow']
    self.context_aggregator = components['context_aggregator']

    # Check if this is S2S pipeline
    is_s2s = components.get('is_s2s', False)

    if is_s2s:
        # Minimal S2S setup - no FlowManager, no IVR
        self.s2s_service = components['s2s_service']

        self.task = PipelineTask(
            self.pipeline,
            params=params,
            enable_tracing=True,
            conversation_id=self.session_id,
            additional_span_attributes={
                "patient.id": self.patient_id,
                "phone.number": self.phone_number,
                "client.name": self.client_name,
                "pipeline.type": "s2s",
            }
        )

        # S2S-specific handlers
        self._setup_s2s_handlers()

    else:
        # Existing standard pipeline setup
        self.ivr_navigator = components['ivr_navigator']

        self.task = PipelineTask(
            self.pipeline,
            params=params,
            enable_tracing=True,
            enable_turn_tracking=True,
            conversation_id=self.session_id,
            additional_span_attributes={
                "patient.id": self.patient_id,
                "phone.number": self.phone_number,
                "client.name": self.client_name,
            }
        )

        self.flow_manager = FlowManager(
            task=self.task,
            llm=components['llm_switcher'],
            context_aggregator=self.context_aggregator,
            transport=self.transport
        )

        self.flow.flow_manager = self.flow_manager
        self.flow.context_aggregator = self.context_aggregator
        self.flow.transport = self.transport
        self.flow.pipeline = self

        setup_transport_handlers(self, self.call_type)
        setup_transcript_handler(self)

        if self.call_type == "dial-out":
            setup_ivr_handlers(self, self.ivr_navigator)

    # ... rest of run() method unchanged (runner.run(self.task)) ...
```

Add S2S handler methods:
```python
def _setup_s2s_handlers(self):
    """Setup minimal handlers for S2S pipeline."""

    @self.transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info(f"S2S: Caller connected - {participant['id']}")
        # OpenAI Realtime starts automatically when it receives audio
        # No FlowManager initialization needed

    @self.transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info(f"S2S: Caller disconnected - {reason}")
        await self._save_s2s_transcript()

    @self.transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("S2S: Client disconnected")
        if not self.transcript_saved:
            await self._save_s2s_transcript()
        await self.task.cancel()

    # Capture transcripts from S2S service
    @self.s2s_service.event_handler("on_transcript_update")
    async def on_transcript_update(service, frame):
        """Capture transcription frames from S2S service."""
        if hasattr(frame, 'text') and frame.text:
            self.transcripts.append({
                "role": "user" if hasattr(frame, 'user_id') else "assistant",
                "content": frame.text,
                "timestamp": datetime.utcnow().isoformat()
            })

async def _save_s2s_transcript(self):
    """Save S2S transcript to database."""
    if self.transcript_saved:
        return

    from handlers.transcript import save_transcript_to_db
    await save_transcript_to_db(self)
    self.transcript_saved = True
```

### Phase 3 Testing

1. Import test: `python -c "from pipeline.runner import ConversationPipeline"`
2. Verify S2S handlers register correctly
3. Test transcript saving logic

---

## Phase 4: Flow Definition for S2S

### Claude Implementation Prompt

```
Implement Phase 4 of the S2S Implementation Plan.

TASK: Create minimal S2S flow definition with get_system_instruction() method.

FILES TO MODIFY:
- clients/demo_clinic_beta/patient_questions/flow_definition.py

REQUIREMENTS:
1. Replace existing flow with simplified S2S version
2. Keep __init__ signature compatible with existing interface (accepts flow_manager, main_llm, etc.)
3. Add get_system_instruction() method that returns the system prompt string
4. System prompt should:
   - Identify as Virtual Assistant from facility
   - Include caller information (name, phone, notes)
   - Give conversational instructions (be helpful, concise, disclose AI status)
   - Instruct to greet caller and ask how to help

5. Remove all node creation methods (create_greeting_node, etc.)
6. Remove all function handlers
7. Keep only __init__ and get_system_instruction()

REFERENCE:
- Study existing flow_definition.py for patient_data structure
- This is a stateless flow - just provides system instruction

DO NOT add any function calling or node-based logic.
```

### Phase 4 Goals

- Create minimal flow class for S2S
- Provide system instruction method
- Remove all FlowManager/node complexity

### Phase 4 Implementation

**File: `clients/demo_clinic_beta/patient_questions/flow_definition.py`**

```python
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class PatientQuestionsFlow:
    """S2S dial-in flow for patient questions - pure conversation, no functions.

    This flow provides only a system instruction for OpenAI Realtime.
    No nodes, no function calling, just helpful conversation.
    """

    def __init__(
        self,
        patient_data: Dict[str, Any],
        flow_manager=None,
        main_llm=None,
        classifier_llm=None,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None
    ):
        """Initialize flow with patient data.

        Args:
            patient_data: Caller/patient information
            flow_manager: Not used in S2S mode (kept for interface compatibility)
            main_llm: Not used in S2S mode
            classifier_llm: Not used in S2S mode
            context_aggregator: Not used in S2S mode
            transport: Not used in S2S mode
            pipeline: Not used in S2S mode
            organization_id: Organization identifier
        """
        self.patient_data = patient_data
        self.organization_id = organization_id

        # These are not used in S2S mode but kept for interface compatibility
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline

    def get_system_instruction(self) -> str:
        """Get the system instruction for OpenAI Realtime.

        Returns:
            System prompt string for the S2S conversation
        """
        patient_name = self.patient_data.get('patient_name', '')
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        patient_phone = self.patient_data.get('patient_phone', '')
        caller_phone = self.patient_data.get('caller_phone', patient_phone)
        notes = self.patient_data.get('notes', '')

        return f"""You are a Virtual Assistant from {facility_name}.

CALLER INFORMATION:
- The caller may be: {patient_name}
- Phone: {caller_phone}
- Notes: {notes}

INSTRUCTIONS:
- Be helpful, friendly, and have a natural conversation
- Answer questions about the clinic, appointments, or general inquiries
- Keep responses concise since this is a phone call (1-2 sentences usually)
- If you cannot help with something, politely suggest they speak with clinic staff
- Always disclose that you are a Virtual Assistant when asked
- Speak only in English

Start by greeting the caller warmly and asking how you can help them today."""
```

### Phase 4 Testing

1. Import test: `python -c "from clients.demo_clinic_beta.patient_questions.flow_definition import PatientQuestionsFlow"`
2. Verify get_system_instruction() returns valid string
3. Check patient_data fields are interpolated correctly

---

## Phase 5: Services Configuration

### Claude Implementation Prompt

```
Implement Phase 5 of the S2S Implementation Plan.

TASK: Add S2S configuration to services.yaml for patient_questions workflow.

FILES TO MODIFY:
- clients/demo_clinic_beta/patient_questions/services.yaml

REQUIREMENTS:
1. Keep existing call_type: "dial-in"
2. Add 's2s' section with:
   - provider: openai-realtime
   - model: gpt-4o-realtime-preview
   - api_key: ${OPENAI_API_KEY}
   - voice_id: alloy (or another OpenAI voice)
3. Keep existing services section (for potential fallback/future use)
4. The presence of 's2s' key triggers S2S pipeline in PipelineFactory

REFERENCE:
- Study existing services.yaml structure
- Environment variables use ${VAR_NAME} syntax

DO NOT remove existing services configuration.
```

### Phase 5 Goals

- Configure S2S service in services.yaml
- Trigger S2S pipeline routing
- Maintain fallback services

### Phase 5 Implementation

**File: `clients/demo_clinic_beta/patient_questions/services.yaml`**

```yaml
call_type: "dial-in"

# S2S configuration - triggers minimal pipeline
# When present with call_type: "dial-in", PipelineFactory uses _build_s2s_pipeline
s2s:
  provider: openai-realtime
  model: gpt-4o-realtime-preview
  api_key: ${OPENAI_API_KEY}
  voice_id: alloy  # Options: alloy, echo, fable, onyx, nova, shimmer

# Keep standard services for potential fallback or future use
services:
  stt:
    api_key: ${DEEPGRAM_API_KEY}
    model: flux-general-en
    eager_eot_threshold: 0.55
    eot_threshold: 0.65
    eot_timeout_ms: 3500

  llm:
    provider: openai
    model: gpt-4o-mini
    temperature: 0.4
    api_key: ${OPENAI_API_KEY}

  classifier_llm:
    provider: groq
    model: llama-3.3-70b-versatile
    temperature: 0
    max_tokens: 10
    api_key: ${GROQ_API_KEY}

  tts:
    provider: cartesia
    model: sonic-3-2025-10-27
    voice_id: 421b3369-f63f-4b03-8980-37a44df1d4e8
    api_key: ${CARTESIA_API_KEY}

  transport:
    provider: daily
    api_key: ${DAILY_API_KEY}
```

### Phase 5 Testing

1. Verify YAML syntax: `python -c "import yaml; yaml.safe_load(open('clients/demo_clinic_beta/patient_questions/services.yaml'))"`
2. Check s2s section is present
3. Verify environment variable syntax

---

## Phase 6: Integration Testing

### Claude Implementation Prompt

```
Implement Phase 6 of the S2S Implementation Plan.

TASK: End-to-end testing of S2S pipeline.

TESTING STEPS:
1. Start backend server: python app.py (port 8000)
2. Start bot server: python bot.py (port 7860)
3. Configure Daily webhook to point to local ngrok URL
4. Make test call to dial-in number

VERIFICATION:
- Check logs for "S2S: Caller connected"
- Verify conversation flows naturally
- Check MongoDB for saved transcript
- Verify Langfuse traces show pipeline.type: s2s

TROUBLESHOOTING:
- If no audio: Check OpenAI API key and model name
- If no response: Check system instruction is being passed
- If transcript empty: Check event handler registration

DO NOT modify code in this phase - testing only.
```

### Phase 6 Goals

- Verify end-to-end S2S functionality
- Confirm transcript persistence
- Validate observability integration

### Phase 6 Testing Checklist

**Local Development Testing:**

1. [ ] Backend server starts without errors
2. [ ] Bot server starts without errors
3. [ ] S2S pipeline is selected for patient_questions workflow
4. [ ] Daily webhook receives incoming call
5. [ ] Bot joins room successfully
6. [ ] Caller hears greeting from OpenAI Realtime
7. [ ] Conversation flows naturally (back and forth)
8. [ ] Interruptions work correctly
9. [ ] Call disconnection handled gracefully
10. [ ] Transcript saved to MongoDB
11. [ ] Langfuse trace shows pipeline.type: s2s

**Regression Testing (Dial-Out):**

1. [ ] prior_auth workflow still uses standard pipeline
2. [ ] IVR navigation works correctly
3. [ ] Function calling works
4. [ ] Transcripts saved correctly

---

## File Summary

| File | Changes |
|------|---------|
| `services/service_factory.py` | Add create_s2s() method (~35 lines) |
| `pipeline/pipeline_factory.py` | Add S2S routing in build(), add _build_s2s_pipeline() (~80 lines) |
| `pipeline/runner.py` | Add S2S branch in run(), add _setup_s2s_handlers(), _save_s2s_transcript() (~70 lines) |
| `clients/demo_clinic_beta/patient_questions/flow_definition.py` | Replace with minimal S2S flow (~60 lines) |
| `clients/demo_clinic_beta/patient_questions/services.yaml` | Add s2s section (~8 lines) |

**Total: ~250 lines of new/modified code**

---

## Environment Variables

### Required for S2S

- `OPENAI_API_KEY` - Used by S2S service for OpenAI Realtime
- `DAILY_API_KEY` - Used by transport (unchanged)

### Existing Variables (Unchanged)

All existing environment variables remain the same. No new variables needed beyond existing OpenAI key.

---

## Troubleshooting Guide

### Common Issues

**"S2S service not created"**
- Check services.yaml has 's2s' section
- Verify call_type is "dial-in"
- Check OPENAI_API_KEY is set

**"No audio from bot"**
- Verify model name is correct (gpt-4o-realtime-preview)
- Check OpenAI API key has Realtime API access
- Verify session_properties are configured correctly

**"Transcripts not saving"**
- Check event handlers are registered
- Verify MongoDB connection
- Check transcript_saved flag logic

**"Pipeline type not showing in Langfuse"**
- Verify additional_span_attributes includes "pipeline.type": "s2s"
- Check Langfuse credentials are set

### Debugging Tips

1. Add debug logging in _build_s2s_pipeline() to confirm S2S path is taken
2. Log session_properties before creating S2S service
3. Add logging in event handlers to trace flow
4. Check Daily dashboard for room/participant status

---

## Future Enhancements

After basic S2S works:

1. **Function calling** - Add tools to S2S service for appointment booking
2. **Context persistence** - Carry context across multiple calls
3. **Voice selection** - Allow per-organization voice configuration
4. **Fallback** - Fall back to standard pipeline if S2S fails
5. **Metrics** - Track S2S-specific metrics (latency, turn detection accuracy)
