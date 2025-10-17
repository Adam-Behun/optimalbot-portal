"""
Schema-Based Pipeline - Correct implementation using task.queue_frames()
Uses documented Pipecat context management approaches
"""

from pipecat.frames.frames import (
    Frame, 
    LLMMessagesAppendFrame,
    LLMMessagesUpdateFrame,
    TranscriptionFrame,
    FunctionCallResultFrame,
    FunctionCallInProgressFrame,
    EndFrame,
    TTSSpeakFrame,
    EndTaskFrame,
    VADParamsUpdateFrame
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.transports.services.daily import DailyTransport, DailyParams
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext 
from pipecat.extensions.voicemail.voicemail_detector import VoicemailDetector
from pipecat.extensions.ivr.ivr_navigator import IVRNavigator, IVRStatus

from deepgram import LiveOptions

# Local imports
from functions import PATIENT_TOOLS, update_prior_auth_status_handler
from models import get_async_patient_db
from audio_processors import AudioResampler, DropEmptyAudio, StateTagStripper
from engine import ConversationContext
from monitoring import emit_event

import os
import sys
import json
import logging
import traceback
from typing import Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class CustomPipelineRunner(PipelineRunner):
    """Custom runner that handles Windows signal issues"""
    def _setup_sigint(self):
        if sys.platform == 'win32':
            logger.warning("Signal handling not supported on Windows")
            return
        super()._setup_sigint()


class SchemaBasedPipeline:
    """
    Schema-driven voice AI pipeline using Daily.co telephony.
    State transitions happen dynamically based on conversation flow.
    
    Key Implementation Details:
    - Uses schema-based context management (schema.yaml, prompts.yaml)
    - Monitors transcripts to detect when context should change
    - Uses task.queue_frames() to update system prompts (documented approach)
    - Reactive to insurance rep's questions (not bot-controlled flow)
    """
    
    def __init__(
        self, 
        session_id: str,
        patient_id: str,
        patient_data: Dict[str, Any],
        conversation_schema,
        data_formatter,
        phone_number: str,
        services_config: Dict[str, Any],
        debug_mode: bool = False
    ):
        self.session_id = session_id
        self.patient_id = patient_id
        self.patient_data = patient_data
        self.conversation_schema = conversation_schema
        self.data_formatter = data_formatter
        self.phone_number = phone_number
        self.services_config = services_config
        self.debug_mode = debug_mode
        
        # Pipeline components
        self.transport = None
        self.pipeline = None
        self.runner = None
        self.llm = None
        self.task = None
        self.conversation_context = None
        self.voicemail_detector = None
        self.ivr_navigator = None
        
        # Transcript tracking
        self.transcripts = []
        self.transcript_processor = TranscriptProcessor()
        
        logger.info(f"=== SCHEMA PIPELINE INITIALIZATION ===")
        logger.info(f"Session ID: {session_id}")
        logger.info(f"Patient ID: {patient_id}")
        logger.info(f"Patient: {patient_data.get('name')}")
        logger.info(f"Phone Number: {phone_number}")
        logger.info(f"Schema: {conversation_schema.conversation.name} v{conversation_schema.conversation.version}")
        
        emit_event(
            session_id=self.session_id,
            category="CALL",
            event="call_started",
            metadata={
                "patient_id": patient_id,
                "patient_name": patient_data.get("name"),
                "phone_number": phone_number,
                "schema_version": conversation_schema.conversation.version
            }
        )
    
    def create_pipeline(self, url: str, token: str, room_name: str):
        """Create the Pipecat pipeline following documented approach"""
        logger.info(f"=== CREATING SCHEMA PIPELINE ===")
        logger.info(f"Room: {room_name}")
        
        vad_analyzer = SileroVADAnalyzer(params=VADParams(
            confidence=0.7,
            start_secs=0.2,
            stop_secs=0.8,
            min_volume=0.5
        ))
        
        # Transport
        logger.info("Creating DailyTransport...")
        transport_config = self.services_config['services']['transport']
        if transport_config['provider'] == 'daily':
            self.transport = DailyTransport(
                url,
                token,
                room_name,
                params=DailyParams(
                    audio_in_sample_rate=16000,
                    audio_in_channels=1,
                    audio_out_enabled=True,
                    audio_out_sample_rate=24000,
                    audio_out_channels=1,
                    transcription_enabled=False,
                    vad_analyzer=vad_analyzer,
                    vad_enabled=True,
                    vad_audio_passthrough=True,
                    api_key=transport_config['api_key'],
                    phone_number_id=transport_config['phone_number_id']
                )
            )
        
        self._setup_dialout_handlers()
        logger.info("✅ Dialout handlers registered")
        
        logger.info("=== INITIALIZING SERVICES ===")
        
        # STT
        logger.info("Creating STT...")
        stt_config = self.services_config['services']['stt']
        if stt_config['provider'] == 'deepgram':
            self.stt = DeepgramSTTService(
                api_key=stt_config['api_key'],
                model=stt_config['model'],
                options=LiveOptions(endpointing=stt_config['endpointing'])
            )

        # Classifier LLM for voicemail detection
        logger.info("Creating Classifier LLM for voicemail detection...")
        classifier_config = self.services_config['services']['classifier_llm']
        classifier_llm = OpenAILLMService(
            api_key=classifier_config['api_key'],
            model=classifier_config['model'],
            temperature=classifier_config['temperature']
        )

        # Load voicemail prompt from schema
        voicemail_prompt = None
        if hasattr(self.conversation_schema, 'prompts'):
            prompts_dict = self.conversation_schema.prompts.get("prompts", {})
            voicemail_config = prompts_dict.get("voicemail_detection", {})
            voicemail_prompt = voicemail_config.get("system")
            
            if voicemail_prompt:
                logger.info(f"✅ Loaded voicemail prompt ({len(voicemail_prompt)} chars)")
            else:
                logger.warning("⚠️ Could not load voicemail prompt from schema")
        else:
            logger.warning("⚠️ Schema has no 'prompts' attribute")

        self.voicemail_detector = VoicemailDetector(
            llm=classifier_llm,
            voicemail_response_delay=0.8,
            custom_system_prompt=voicemail_prompt
        )

        logger.info(f"✅ VoicemailDetector initialized:")
        logger.info(f"   - Classifier LLM: {classifier_config['model']}")
        logger.info(f"   - Temperature: {classifier_config['temperature']}")
        logger.info(f"   - Response delay: 0.8s")
        
        # Conversation Context
        logger.info("=== INITIALIZING CONVERSATION CONTEXT ===")
        self.conversation_context = ConversationContext(
            schema=self.conversation_schema,
            patient_data=self.patient_data,
            session_id=self.session_id
        )
        logger.info(f"Context initialized - Initial state: {self.conversation_context.current_state}")
        
        # Main LLM (will be wrapped by IVRNavigator)
        logger.info("Creating main LLM...")
        llm_config = self.services_config['services']['llm']

        if llm_config['provider'] == 'openai':
            main_llm = OpenAILLMService(
                api_key=llm_config['api_key'],
                model=llm_config['model'],
                temperature=llm_config['temperature']
            )

        # Register functions on the main LLM
        # These functions will only be available during conversation, not during IVR navigation
        main_llm.register_function(
            "update_prior_auth_status",
            update_prior_auth_status_handler
        )
        logger.info("✅ Registered update_prior_auth_status function on main LLM")

        # IVRNavigator wraps the main LLM (per documentation)
        logger.info("Creating IVRNavigator (wraps main LLM)...")
        ivr_goal = "Navigate to a human representative for eligibility and benefits verification"
        ivr_vad_params = VADParams(stop_secs=2.0)

        ivr_goal = "Navigate to provider services for eligibility verification"
        if hasattr(self.conversation_schema, 'prompts'):
            prompts_dict = self.conversation_schema.prompts.get("prompts", {})
            ivr_config = prompts_dict.get("ivr_navigation", {})
            
            # Extract the task section which describes the goal
            ivr_task = ivr_config.get("task", "")
            if ivr_task and hasattr(self.data_formatter, 'render_template'):
                ivr_goal = self.data_formatter.render_template(ivr_task, self.patient_data)

        self.ivr_navigator = IVRNavigator(
            llm=main_llm,
            ivr_prompt=ivr_goal,  # This gets inserted into the base template
            ivr_vad_params=ivr_vad_params
        )

        logger.info(f"✅ IVRNavigator initialized:")
        logger.info(f"   - Main LLM: {llm_config['model']}")
        logger.info(f"   - IVR Goal: {ivr_goal}")
        logger.info(f"   - IVR VAD stop_secs: {ivr_vad_params.stop_secs}s")

        # Store LLM reference (for context aggregators)
        self.llm = main_llm

        # Get initial prompt from conversation context (connection state)
        initial_prompt = self.conversation_context.render_prompt()

        # Create context with tools
        llm_context = OpenAILLMContext(
            messages=[{"role": "system", "content": initial_prompt}],
            tools=PATIENT_TOOLS
        )
        self.context_aggregators = self.llm.create_context_aggregator(llm_context)
        
        # TTS
        logger.info("Creating TTS...")
        tts_config = self.services_config['services']['tts']
        if tts_config['provider'] == 'elevenlabs':
            self.tts = ElevenLabsTTSService(
                api_key=tts_config['api_key'],
                voice_id=tts_config['voice_id'],
                model=tts_config['model'],
                stability=tts_config['stability']
            )
        
        # Setup event handlers
        self._setup_transcript_handler()
        self._setup_voicemail_handlers()
        self._setup_ivr_handlers() 

        logger.info("=== BUILDING PIPELINE ===")
        # IVRNavigator REPLACES the LLM in the pipeline (per documentation)
        self.pipeline = Pipeline([
            self.transport.input(),
            AudioResampler(target_sample_rate=16000),
            DropEmptyAudio(),
            self.stt,
            self.voicemail_detector.detector(),
            self.transcript_processor.user(),
            self.context_aggregators.user(),
            self.ivr_navigator,  # Navigator here (not self.llm)
            StateTagStripper(),
            self.tts,
            self.voicemail_detector.gate(),
            self.transcript_processor.assistant(),
            self.context_aggregators.assistant(),
            self.transport.output()
        ])
        
        logger.info(f"Pipeline components ({len(self.pipeline.processors)}):")
        for i, proc in enumerate(self.pipeline.processors, 1):
            logger.info(f"{i}. {proc.__class__.__name__}")

        logger.info("=== COMPONENT POSITIONS IN PIPELINE ===")
        detector_pos = None
        gate_pos = None
        ivr_pos = None

        for i, proc in enumerate(self.pipeline.processors):
            if 'VoicemailDetector' in proc.__class__.__name__:
                detector_pos = i
            elif 'TTSGate' in proc.__class__.__name__ or 'Gate' in proc.__class__.__name__:
                gate_pos = i
            elif 'IVRNavigator' in proc.__class__.__name__:
                ivr_pos = i

        logger.info(f"VoicemailDetector.detector() position: {detector_pos}")
        logger.info(f"IVRNavigator position: {ivr_pos}")
        logger.info(f"VoicemailDetector.gate() position: {gate_pos}")

        if detector_pos and gate_pos:
            logger.info(f"✅ Gate is {gate_pos - detector_pos} positions after detector (correct)")
        else:
            logger.error("❌ Could not verify detector/gate positions!")
        
        logger.info("✅ Schema pipeline created successfully")

    def _setup_transcript_handler(self):
        """Monitor transcripts for state transitions and completion"""
        logger.info("🔧 Setting up transcript handler...")
        
        @self.transcript_processor.event_handler("on_transcript_update")
        async def handle_transcript_update(processor, frame):
            logger.info(f"🎤 Transcript handler called with {len(frame.messages)} messages")
            for message in frame.messages:
                transcript_entry = {
                    "role": message.role,
                    "content": message.content,
                    "timestamp": message.timestamp or datetime.now().isoformat(),
                    "type": "transcript"
                }
                self.transcripts.append(transcript_entry)
                logger.info(f"[TRANSCRIPT] {transcript_entry['role']}: {transcript_entry['content']}")
                
                if message.role == "user":
                    await self._check_state_transition(message.content)  # Keep existing keyword-based
                elif message.role == "assistant":
                    await self._check_assistant_state_transition(message.content)  # NEW: LLM-directed
                    await self._check_for_call_completion()

    def _setup_voicemail_handlers(self):
        """Setup VoicemailDetector event handlers"""
        logger.info("🔧 Setting up voicemail handlers...")
        
        @self.voicemail_detector.event_handler("on_voicemail_detected")
        async def handle_voicemail(processor):
            # ✅ ADD ENTRY LOG WITH TIMESTAMP
            start_time = datetime.now()
            logger.info("=" * 60)
            logger.info(f"📞 [VOICEMAIL DETECTED] Event fired at {start_time.isoformat()}")
            logger.info(f"   Current state: {self.conversation_context.current_state}")
            logger.info(f"   Session: {self.session_id}")
            logger.info(f"   Processor type: {type(processor).__name__}")
            logger.info("=" * 60)
            
            emit_event(
                session_id=self.session_id,
                category="DETECTION",
                event="voicemail_detected",
                metadata={"phone_number": self.phone_number}
            )
            
            # Transition to voicemail state
            logger.info("📞 [STEP 1/4] Transitioning to voicemail_detected state...")
            await self._transition_to_state("voicemail_detected", "voicemail_system_detected")
            logger.info("✅ [STEP 1/4] State transition complete")
            
            # Get voicemail message with robust error handling
            logger.info("📞 [STEP 2/4] Extracting voicemail message from schema...")
            message = "Hello, this was Alexandra trying to reach out regarding eligibility and benefits verification. Thank you."
            
            try:
                if hasattr(self.conversation_schema, 'prompts'):
                    logger.debug(f"   Schema has prompts attribute: {type(self.conversation_schema.prompts)}")
                    voicemail_prompt = self.conversation_schema.prompts.get("voicemail_detected", {})
                    logger.debug(f"   Voicemail prompt keys: {list(voicemail_prompt.keys())}")
                    
                    extracted_message = voicemail_prompt.get("message", "")
                    logger.info(f"   Extracted message length: {len(extracted_message)} chars")
                    
                    if extracted_message:
                        if hasattr(self.data_formatter, 'render_template'):
                            logger.debug("   Rendering message template...")
                            message = self.data_formatter.render_template(extracted_message, self.patient_data)
                            logger.info("✅ [STEP 2/4] Using rendered message from schema")
                        else:
                            message = extracted_message
                            logger.info("✅ [STEP 2/4] Using raw message from schema (no template rendering)")
                    else:
                        logger.warning("⚠️ [STEP 2/4] No message in schema, using default")
                else:
                    logger.warning(f"⚠️ Schema has no 'prompts' attribute (type: {type(self.conversation_schema)})")
            except Exception as e:
                logger.error(f"❌ [STEP 2/4] Error extracting voicemail message: {e}")
                logger.error(traceback.format_exc())
            
            logger.info(f"📞 Final voicemail message: '{message}'")
            
            logger.info("📞 [STEP 3/4] Queuing TTS frame...")
            await processor.push_frame(TTSSpeakFrame(message))
            logger.info("✅ [STEP 3/4] TTSSpeakFrame queued")
            
            # Update DB to Failed (voicemail = unsuccessful call)
            logger.info("📞 [STEP 4/4] Updating database and ending call...")
            await get_async_patient_db().update_call_status(self.patient_id, "Completed - Left VM")
            logger.info("✅ Call status updated: Completed - Left VM")
            
            # End the call
            if self.task:
                await self.task.queue_frames([EndFrame()])
                logger.info("✅ EndFrame queued - call will end")
            else:
                logger.error("❌ Cannot queue EndFrame: task not initialized")
            
            # ✅ ADD EXIT LOG WITH DURATION
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            logger.info("=" * 60)
            logger.info(f"📞 [VOICEMAIL HANDLER COMPLETE] Duration: {duration:.3f}s")
            logger.info("=" * 60)
            
    def _setup_ivr_handlers(self):
        """Setup IVRNavigator event handlers per Pipecat documentation"""
        logger.info("🔧 Setting up IVR handlers...")
        
        @self.ivr_navigator.event_handler("on_conversation_detected")
        async def on_conversation_detected(processor, conversation_history):
            """
            Handle when a human answers directly (no IVR system).
            Per docs: Use LLMMessagesUpdateFrame with run_llm=True to start conversation.
            """
            logger.info("=" * 80)
            logger.info(f"👤 [CONVERSATION DETECTED] Human answered directly")
            logger.info(f"   Current state: {self.conversation_context.current_state}")
            logger.info(f"   Conversation history: {len(conversation_history) if conversation_history else 0} messages")
            if conversation_history:
                logger.debug(f"   History preview: {conversation_history[:2]}")
            logger.info("=" * 80)
            
            emit_event(
                session_id=self.session_id,
                category="DETECTION",
                event="conversation_detected",
                metadata={"phone_number": self.phone_number}
            )
            
            # Transition to greeting state
            logger.info("👤 [STEP 1/3] Transitioning to greeting state...")
            await self._transition_to_state("greeting", "human_answered_directly")
            
            # Get greeting prompt from schema
            logger.info("👤 [STEP 2/3] Building conversation context...")
            greeting_prompt = self.conversation_context.render_prompt()
            
            # Build messages array with system prompt + conversation history
            messages = [{"role": "system", "content": greeting_prompt}]
            
            # Preserve conversation history if available
            if conversation_history:
                messages.extend(conversation_history)
                logger.info(f"   Preserved {len(conversation_history)} conversation messages")
            
            # Update context and trigger LLM to start conversation (per docs)
            logger.info("👤 [STEP 3/3] Updating LLM context with run_llm=True...")
            if self.task:
                await self.task.queue_frames([
                    LLMMessagesUpdateFrame(messages=messages, run_llm=True),
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                ])
                logger.info("✅ LLM context updated - conversation mode active")
                logger.info("✅ VAD updated: stop_secs=0.8 (conversation mode)")
            else:
                logger.error("❌ Cannot update context: task not initialized")
            
            logger.info("=" * 80)
            logger.info("✅ [CONVERSATION DETECTED] Handler complete")
            logger.info("=" * 80)
        
        @self.ivr_navigator.event_handler("on_ivr_status_changed")
        async def on_ivr_status_changed(processor, status):
            """
            Handle IVR navigation status changes.
            Per docs: DETECTED and COMPLETED transitions are automatic,
            but we handle them for state management and logging.
            """
            logger.info("=" * 80)
            logger.info(f"🤖 [IVR STATUS CHANGE] {status}")
            logger.info(f"   Current state: {self.conversation_context.current_state}")
            logger.info(f"   Status type: {type(status).__name__}")
            logger.info(f"   Status value: {status.value if hasattr(status, 'value') else status}")
            logger.info("=" * 80)
            
            emit_event(
                session_id=self.session_id,
                category="DETECTION",
                event="ivr_status_changed",
                metadata={"status": str(status), "phone_number": self.phone_number}
            )
            
            if status == IVRStatus.DETECTED:
                """
                IVR system detected - navigator will automatically handle navigation.
                We just update our state tracking and optionally the context.
                """
                logger.info("🤖 [IVR DETECTED] IVR system detected - beginning navigation")
                logger.info(f"   Navigation goal: Reach human for eligibility verification")
                logger.info(f"   Navigator will automatically use IVR prompt and VAD params")
                
                # Update our state tracking
                await self._transition_to_state("ivr_navigation", "ivr_system_detected")
                
                # Optional: Update context with IVR navigation prompt from schema
                # The navigator already has its own IVR prompt, but we can sync our state
                logger.info("   Syncing IVR navigation prompt with schema...")
                ivr_prompt = self.conversation_context.render_prompt()
                
                if self.task:
                    # Update context but don't run LLM (navigator handles that)
                    await self.task.queue_frames([
                        LLMMessagesUpdateFrame(
                            messages=[{"role": "system", "content": ivr_prompt}],
                            run_llm=False
                        )
                    ])
                    logger.info("✅ Context synced with ivr_navigation state")
                
                logger.info("✅ [IVR DETECTED] Transition complete - navigator is in control")
            
            elif status == IVRStatus.COMPLETED:
                """
                IVR navigation completed successfully - reached target department.
                Per docs: Update context with conversation prompt and trigger LLM.
                """
                logger.info("✅ [IVR COMPLETED] Successfully navigated to human representative")
                
                # Transition to greeting state
                logger.info("   [STEP 1/3] Transitioning to greeting state...")
                await self._transition_to_state("greeting", "ivr_navigation_complete")
                
                # Get greeting prompt from schema
                logger.info("   [STEP 2/3] Building conversation context...")
                greeting_prompt = self.conversation_context.render_prompt()
                
                # Build messages for conversation
                messages = [{"role": "system", "content": greeting_prompt}]
                
                # Update context and trigger LLM to start conversation (per docs)
                logger.info("   [STEP 3/3] Updating LLM context with run_llm=True...")
                if self.task:
                    await self.task.queue_frames([
                        LLMMessagesUpdateFrame(messages=messages, run_llm=True),
                        VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                    ])
                    logger.info("✅ LLM context updated - conversation mode active")
                    logger.info("✅ VAD updated: stop_secs=0.8 (conversation mode)")
                else:
                    logger.error("❌ Cannot update context: task not initialized")
                
                logger.info("=" * 80)
                logger.info("✅ [IVR COMPLETED] Ready for conversation")
                logger.info("=" * 80)
            
            elif status == IVRStatus.STUCK:
                """
                IVR navigation failed - cannot find path forward.
                Per docs: Log the issue, speak error message, and terminate.
                """
                logger.warning("=" * 80)
                logger.warning("⚠️ [IVR STUCK] Navigation failed - ending call")
                logger.warning(f"   State when stuck: {self.conversation_context.current_state}")
                logger.warning("=" * 80)
                
                # Transition to stuck state
                await self._transition_to_state("ivr_stuck", "ivr_navigation_failed")
                
                # Get stuck message from schema
                logger.info("   Extracting IVR stuck message from schema...")
                message = "I apologize, but I'm unable to navigate to the appropriate department at this time. We will try reaching out again later. Thank you."
                
                try:
                    if hasattr(self.conversation_schema, 'prompts'):
                        prompts_dict = self.conversation_schema.prompts.get("prompts", {})
                        ivr_stuck_config = prompts_dict.get("ivr_stuck", {})
                        extracted_message = ivr_stuck_config.get("message", "")
                        
                        if extracted_message:
                            if hasattr(self.data_formatter, 'render_template'):
                                message = self.data_formatter.render_template(
                                    extracted_message, 
                                    self.patient_data
                                )
                                logger.info("✅ Using rendered message from schema")
                            else:
                                message = extracted_message
                                logger.info("✅ Using raw message from schema")
                        else:
                            logger.warning("⚠️ No message in schema, using default")
                except Exception as e:
                    logger.error(f"❌ Error extracting IVR stuck message: {e}")
                    logger.error(traceback.format_exc())
                
                logger.info(f"   Final stuck message: '{message}'")
                
                # Speak error message and end call
                if self.task:
                    await self.task.queue_frames([
                        TTSSpeakFrame(message),
                        EndFrame()
                    ])
                    logger.info("✅ Stuck message queued - call will end")
                else:
                    logger.error("❌ Cannot queue frames: task not initialized")
                
                # Update database
                await get_async_patient_db().update_call_status(self.patient_id, "Failed")
                logger.error("❌ Call status: Failed (IVR navigation stuck)")
                
                logger.warning("=" * 80)
                logger.warning("⚠️ [IVR STUCK] Handler complete - call terminating")
                logger.warning("=" * 80)
            
            else:
                # Unexpected status
                logger.warning(f"⚠️ [IVR STATUS] Unexpected status: {status}")
                logger.warning(f"   This may indicate a new IVR status type or version mismatch")
                
    def _setup_function_call_handler(self):
        """Setup handler for LLM function calls"""
        
        @self.llm.event_handler("on_function_call")
        async def handle_function_call(llm, function_name, arguments):
            """Execute function calls from LLM"""
            logger.info(f"🔧 [FUNCTION CALL] {function_name}")
            logger.info(f"   Arguments: {arguments}")
            
            try:
                # Get function from registry
                func = FUNCTION_REGISTRY.get(function_name)
                if not func:
                    logger.error(f"Function not found: {function_name}")
                    return {"error": f"Function {function_name} not found"}
                
                # Add patient_id if not in arguments
                if "patient_id" not in arguments:
                    arguments["patient_id"] = self.patient_id
                
                # Execute function
                result = await func(**arguments)
                
                logger.info(f"✅ [FUNCTION RESULT] {function_name}: {result}")
                
                emit_event(
                    session_id=self.session_id,
                    category="FUNCTION",
                    event="function_executed",
                    metadata={
                        "function_name": function_name,
                        "arguments": arguments,
                        "result": result
                    }
                )
                
                return {"success": result}
                
            except Exception as e:
                logger.error(f"❌ [FUNCTION ERROR] {function_name}: {e}")
                logger.error(traceback.format_exc())
                return {"error": str(e)}
    
    async def _check_state_transition(self, user_message: str):
        """
        Check if user message should trigger a state transition.
        
        This uses the transition rules defined in schema.yaml, so you can
        configure keywords and transition logic without touching Python code.
        """
        
        current_state = self.conversation_context.current_state
        
        logger.info(f"🎯 [STATE={current_state}] Checking: '{user_message[:50]}'")
        
        # ✅ Check transition using schema rules (configured in YAML)
        transition = self.conversation_schema.check_transition(current_state, user_message)
        
        if transition:
            logger.info(f"✅ Transition rule matched: {transition.description}")
            logger.info(f"   From: {transition.from_state} → To: {transition.to_state}")
            logger.info(f"   Trigger: {transition.trigger.type} (keywords: {transition.trigger.keywords})")
            
            await self._transition_to_state(
                transition.to_state,
                transition.reason
            )
        else:
            logger.debug(f"No transition rule matched for current state: {current_state}")

    async def _check_assistant_state_transition(self, assistant_message: str):
        """
        Parse assistant's response for LLM-directed state transition requests.
        Validates transitions against allowed_transitions for compliance.
        
        Format expected: <next_state>STATE_NAME</next_state>
        """
        import re
        
        # Extract <next_state> tag
        match = re.search(r'<next_state>(\w+)</next_state>', assistant_message, re.IGNORECASE)
        
        if not match:
            # No transition requested - normal behavior
            return
        
        requested_state = match.group(1).lower()
        current_state = self.conversation_context.current_state
        
        logger.info("=" * 80)
        logger.info(f"🤖 [LLM TRANSITION REQUEST]")
        logger.info(f"   Current state: {current_state}")
        logger.info(f"   Requested state: {requested_state}")
        logger.info(f"   Assistant message: {assistant_message[:150]}...")
        
        # Check if current state allows LLM direction
        if not self.conversation_schema.is_llm_directed(current_state):
            logger.debug(f"   State {current_state} is not LLM-directed - ignoring request")
            logger.info("=" * 80)
            return
        
        # Get allowed transitions from schema
        allowed_transitions = self.conversation_schema.get_allowed_transitions(current_state)
        logger.info(f"   Allowed transitions: {allowed_transitions}")
        
        # Validate transition
        if requested_state in allowed_transitions:
            logger.info(f"✅ Transition ALLOWED - executing")
            await self._transition_to_state(requested_state, "llm_directed")
            
            emit_event(
                session_id=self.session_id,
                category="STATE",
                event="llm_directed_transition",
                metadata={
                    "from_state": current_state,
                    "to_state": requested_state,
                    "allowed_transitions": allowed_transitions,
                    "assistant_message_preview": assistant_message[:200]
                }
            )
            logger.info("=" * 80)
        else:
            logger.warning(f"⚠️ Transition BLOCKED - {requested_state} not in allowed list")
            logger.warning(f"   LLM will remain in {current_state}")
            
            emit_event(
                session_id=self.session_id,
                category="STATE",
                event="llm_transition_blocked",
                severity="warning",
                metadata={
                    "from_state": current_state,
                    "requested_state": requested_state,
                    "allowed_transitions": allowed_transitions,
                    "assistant_message": assistant_message[:300]
                }
            )
            logger.info("=" * 80)

    async def _check_for_call_completion(self):
        """Check if closing state + goodbye said → terminate pipeline"""
        if self.conversation_context.current_state != "closing":
            return
        
        assistant_messages = [t for t in self.transcripts if t["role"] == "assistant"]
        if not assistant_messages:
            return
        
        last_msg = assistant_messages[-1]["content"].lower()
        goodbye_phrases = ["goodbye", "have a great day", "thank you so much"]
        
        if any(phrase in last_msg for phrase in goodbye_phrases):
            logger.info("🏁 Closing complete - initiating termination")
            
            # Update DB to Completed
            from models import get_async_patient_db
            await get_async_patient_db().update_call_status(self.patient_id, "Completed")
            logger.info("✅ Call status: Completed")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="call_completed",
                metadata={"patient_id": self.patient_id, "final_state": "closing"}
            )
            
            # Queue EndFrame for graceful shutdown
            if self.task:
                await self.task.queue_frames([EndFrame()])
                logger.info("📤 EndFrame queued - shutdown initiated")
    
    async def _transition_to_state(self, new_state: str, reason: str):
        """
        Perform state transition and update LLM context.
        
        This uses the documented approach from Pipecat's context management:
        https://docs.pipecat.ai/guides/learn/context-management
        """
        
        # ✅ ADD TIMING
        start_time = datetime.now()
        
        if not self.task:
            logger.error("❌ Cannot transition: task not available yet")
            logger.error(f"   Attempted transition: {self.conversation_context.current_state} → {new_state}")
            logger.error(f"   Reason: {reason}")
            return
        
        old_state = self.conversation_context.current_state
        logger.info("=" * 80)
        logger.info(f"🚀 [STATE TRANSITION] {old_state} → {new_state}")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   Session: {self.session_id}")
        logger.info(f"   Task available: {self.task is not None}")
        
        # Handle terminal states
        if new_state in ["connection", "voicemail_detected", "ivr_stuck"]:
            self.conversation_context.transition_to(new_state, reason=reason)
            logger.info(f"✅ Terminal/Special state - no LLM update needed")
            logger.info(f"   State type: {'Terminal' if new_state in ['voicemail_detected', 'ivr_stuck'] else 'Connection'}")
            logger.info("=" * 80)
            return
        
        # ✅ ADD LOG BEFORE TRANSITION
        logger.info(f"   Updating conversation context...")
        
        # Update context state
        self.conversation_context.transition_to(new_state, reason=reason)
        logger.info(f"✅ Context updated to: {self.conversation_context.current_state}")
        
        # Render new prompt
        logger.info(f"   Rendering new prompt for state: {new_state}...")
        new_prompt = self.conversation_context.render_prompt()
        
        # Add patient_id to prompt for insurance_verification state
        if new_state == "insurance_verification":
            new_prompt += f"\n\nIMPORTANT: The patient_id for function calls is: {self.patient_id}"
            logger.info(f"✅ Added patient_id to prompt for insurance_verification")
        
        logger.info(f"📄 [PROMPT] Length: {len(new_prompt)} chars")
        logger.info(f"📄 [PROMPT] Preview: {new_prompt[:150]}...")
        
        # Get current context
        current_context = self.context_aggregators.user().context
        current_messages = current_context.messages if current_context else []
        
        # ✅ ADD CONTEXT PRESERVATION LOGGING
        logger.info(f"📄 [CONTEXT] Current message count: {len(current_messages)}")
        user_msgs = [m for m in current_messages if m.get("role") == "user"]
        assistant_msgs = [m for m in current_messages if m.get("role") == "assistant"]
        system_msgs = [m for m in current_messages if m.get("role") == "system"]
        logger.info(f"📄 [CONTEXT] Messages - User: {len(user_msgs)}, Assistant: {len(assistant_msgs)}, System: {len(system_msgs)}")
        
        # Build new message array
        new_messages = [{"role": "system", "content": new_prompt}]
        
        # Add all non-system messages from current context
        for msg in current_messages:
            if msg.get("role") != "system":
                new_messages.append(msg)
        
        logger.info(f"📄 [CONTEXT] New message array built: {len(new_messages)} total messages")
        logger.info(f"📄 [CONTEXT] Message roles: {[m['role'] for m in new_messages]}")
        
        try:
            # ✅ ADD LOG BEFORE QUEUE
            logger.info(f"   Queueing LLMMessagesUpdateFrame...")
            
            await self.task.queue_frames([
                LLMMessagesUpdateFrame(
                    messages=new_messages,
                    run_llm=False
                )
            ])
            
            # ✅ ADD TIMING LOG
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.info(f"✅ State transition complete: {old_state} → {new_state}")
            logger.info(f"   Duration: {duration:.3f}s")
            logger.info(f"   Conversation history preserved: {len(new_messages) - 1} messages")
            logger.info("=" * 80)
        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"❌ TRANSITION FAILED: {old_state} → {new_state}")
            logger.error(f"   Error: {e}")
            logger.error(f"   Error type: {type(e).__name__}")
            logger.error(traceback.format_exc())
            logger.error("=" * 80)
            
    def _setup_dialout_handlers(self):
        """Setup Daily dial-out event handlers"""
        logger.info("Setting up dialout event handlers...")
        
        @self.transport.event_handler("on_joined")
        async def on_joined(transport, data):
            logger.info(f"=== EVENT: on_joined (Session: {self.session_id}) ===")
            logger.info(f"Bot joined room, initiating dial-out to {self.phone_number}")
            
            try:
                await transport.start_dialout({"phoneNumber": self.phone_number})
                logger.info(f"✅ Dial-out initiated to {self.phone_number}")
                
                emit_event(
                    session_id=self.session_id,
                    category="CALL",
                    event="dialout_initiated",
                    metadata={"phone_number": self.phone_number}
                )
            except Exception as e:
                logger.error(f"❌ Failed to start dial-out: {e}")
                logger.error(traceback.format_exc())
                
                emit_event(
                    session_id=self.session_id,
                    category="CALL",
                    event="dialout_failed",
                    severity="error",
                    metadata={
                        "phone_number": self.phone_number,
                        "error_type": type(e).__name__,
                        "error_message": str(e)
                    }
                )
        
        @self.transport.event_handler("on_dialout_answered")
        async def on_dialout_answered(transport, data):
            logger.info(f"=== EVENT: on_dialout_answered (Session: {self.session_id}) ===")
            logger.info(f"✅ Call answered by {self.phone_number}")
            logger.info(f"Call data: {json.dumps(data, indent=2)}")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="dialout_answered",
                metadata={"phone_number": self.phone_number, "data": data}
            )
        
        @self.transport.event_handler("on_dialout_stopped")
        async def on_dialout_stopped(transport, data):
            logger.info(f"=== EVENT: on_dialout_stopped (Session: {self.session_id}) ===")
            logger.info(f"Call ended: {json.dumps(data, indent=2)}")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="dialout_stopped",
                metadata={"phone_number": self.phone_number, "data": data}
            )

        @self.transport.event_handler("on_participant_left")
        async def on_participant_left(transport, participant, data):  # ✅ Add 'participant' parameter
            """Handle when remote participant hangs up"""
            logger.info(f"=== EVENT: on_participant_left (Session: {self.session_id}) ===")
            logger.info(f"Participant ID: {participant}")
            logger.info(f"Participant data: {data}")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="participant_left",
                metadata={
                    "participant_id": participant,
                    "participant_data": data
                }
            )
            
            try:
                patient = await get_async_patient_db().find_patient_by_id(self.patient_id)
                current_status = patient.get("call_status") if patient else None
                
                if current_status not in ["Completed", "Completed - Left VM", "Failed"]:
                    await get_async_patient_db().update_call_status(self.patient_id, "Completed")
                    logger.info("✅ Call status: Completed (user hung up)")
            except Exception as e:
                logger.error(f"Error updating call status on participant left: {e}")
            
            # Terminate pipeline immediately
            if self.task:
                await self.task.cancel()
                logger.info("📤 EndFrame queued - user hung up, terminating pipeline")
        
        @self.transport.event_handler("on_dialout_error")
        async def on_dialout_error(transport, data):
            logger.error(f"=== EVENT: on_dialout_error (Session: {self.session_id}) ===")
            logger.error(f"Dialout error: {json.dumps(data, indent=2)}")
            
            # Update DB to Failed
            from models import get_async_patient_db
            await get_async_patient_db().update_call_status(self.patient_id, "Failed")
            logger.error("❌ Call status: Failed")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="dialout_error",
                severity="error",
                metadata={"phone_number": self.phone_number, "error_data": data}
            )
        
        logger.info("✅ Dialout handlers registered")
    
    async def run(self, url: str, token: str, room_name: str):
        """Run the schema-based pipeline"""
        logger.info(f"=== RUNNING SCHEMA PIPELINE ===")
        logger.info(f"Session: {self.session_id}")
        logger.info(f"Target Phone: {self.phone_number}")
        
        if not self.pipeline:
            logger.info("Creating pipeline...")
            self.create_pipeline(url, token, room_name)
        
        logger.info("Creating PipelineTask...")
        self.task = PipelineTask(  # ✅ Store task reference
            self.pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
            ),
            conversation_id=self.session_id
        )
        
        logger.info("Creating CustomPipelineRunner...")
        self.runner = CustomPipelineRunner()
        logger.info(f"✅ Starting schema pipeline for session: {self.session_id}")
        
        try:
            await self.runner.run(self.task)
            logger.info("Pipeline completed successfully")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="call_ended",
                metadata={
                    "status": "completed",
                    "phone_number": self.phone_number
                }
            )
            
        except Exception as e:
            logger.error(f"=== PIPELINE ERROR ===")
            logger.error(f"Error: {str(e)}")
            logger.error(traceback.format_exc())
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="call_failed",
                severity="error",
                metadata={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "phone_number": self.phone_number
                }
            )
            
            raise
    
    def get_conversation_state(self) -> Dict[str, Any]:
        """Get current conversation state"""
        if self.conversation_context:
            return {
                "workflow_state": "active",
                "current_state": self.conversation_context.current_state,
                "state_history": getattr(self.conversation_context, 'state_history', []),
                "patient_data": self.patient_data,
                "phone_number": self.phone_number,
                "transcripts": self.transcripts,
            }
        
        return {
            "workflow_state": "inactive",
            "patient_data": self.patient_data,
            "phone_number": self.phone_number,
            "transcripts": []
        }