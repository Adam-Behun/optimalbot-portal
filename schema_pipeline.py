"""
Schema-Based Pipeline - Correct implementation using task.queue_frames()
Uses documented Pipecat context management approaches
"""

from pipecat.frames.frames import (
    Frame, 
    LLMMessagesAppendFrame,
    LLMMessagesUpdateFrame,
    TranscriptionFrame
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
from deepgram import LiveOptions

# Local imports
from audio_processors import AudioResampler, DropEmptyAudio
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
        self.task = None  # ✅ Store task reference for queue_frames
        self.conversation_context = None
        
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
        """Create the Pipecat pipeline with schema-driven components"""
        logger.info(f"=== CREATING SCHEMA PIPELINE ===")
        logger.info(f"Room: {room_name}")
        
        vad_analyzer = SileroVADAnalyzer(params=VADParams(
            confidence=0.7,
            start_secs=0.3,
            stop_secs=0.5,
            min_volume=0.6
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
                    audio_out_sample_rate=16000,
                    audio_out_channels=1,
                    transcription_enabled=False,
                    vad_analyzer=vad_analyzer,
                    vad_enabled=True,
                    vad_audio_passthrough=True,
                    api_key=transport_config['api_key'],  # ✅ Already substituted in app.py
                    phone_number_id=transport_config['phone_number_id']  # ✅ Already substituted
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
                api_key=stt_config['api_key'],  # ✅ Already substituted in app.py
                model=stt_config['model'],
                options=LiveOptions(endpointing=stt_config['endpointing'])
            )
        
        # Conversation Context
        logger.info("=== INITIALIZING CONVERSATION CONTEXT ===")
        self.conversation_context = ConversationContext(
            schema=self.conversation_schema,
            patient_data=self.patient_data,
            session_id=self.session_id
        )
        logger.info(f"Context initialized - Initial state: {self.conversation_context.current_state}")
        
        # LLM with initial context
        logger.info("Creating LLM...")
        llm_config = self.services_config['services']['llm']
        initial_prompt = self.conversation_context.render_prompt()
        
        if llm_config['provider'] == 'openai':
            self.llm = OpenAILLMService(
                api_key=llm_config['api_key'],  # ✅ Already substituted in app.py
                model=llm_config['model'],
                temperature=llm_config['temperature']
            )
        
        # Create context with initial system message
        llm_context = OpenAILLMContext(
            messages=[{"role": "system", "content": initial_prompt}]
        )
        self.context_aggregators = self.llm.create_context_aggregator(llm_context)
        
        # TTS
        logger.info("Creating TTS...")
        tts_config = self.services_config['services']['tts']
        if tts_config['provider'] == 'elevenlabs':
            self.tts = ElevenLabsTTSService(
                api_key=tts_config['api_key'],  # ✅ Already substituted in app.py
                voice_id=tts_config['voice_id'],
                model=tts_config['model'],
                stability=tts_config['stability']
            )
        
        # Setup transcript handler (monitors conversation for state transitions)
        self._setup_transcript_handler()
        
        logger.info("=== BUILDING PIPELINE ===")
        # ✅ Standard Pipecat pipeline order (documented approach)
        self.pipeline = Pipeline([
            self.transport.input(),
            AudioResampler(target_sample_rate=16000),
            DropEmptyAudio(),
            self.stt,
            self.transcript_processor.user(),
            self.context_aggregators.user(),
            self.llm,
            self.tts,
            self.transcript_processor.assistant(),
            self.context_aggregators.assistant(),
            self.transport.output()
        ])
        
        logger.info(f"Pipeline components ({len(self.pipeline.processors)}):")
        for i, proc in enumerate(self.pipeline.processors, 1):
            logger.info(f"{i}. {proc.__class__.__name__}")
        
        logger.info("✅ Schema pipeline created successfully")
    
    def _setup_transcript_handler(self):
        """
        Setup transcript event handler with state transition detection.
        
        This uses the standard Pipecat approach of monitoring transcripts
        via event handlers, as documented in the context management guide.
        """
        
        @self.transcript_processor.event_handler("on_transcript_update")
        async def handle_transcript_update(processor, frame):
            """Monitor user transcripts and trigger state transitions"""
            
            for message in frame.messages:
                # Store transcript
                transcript_entry = {
                    "role": message.role,
                    "content": message.content,
                    "timestamp": message.timestamp or datetime.now().isoformat(),
                    "type": "transcript"
                }
                self.transcripts.append(transcript_entry)
                logger.info(f"[TRANSCRIPT] {transcript_entry['role']}: {transcript_entry['content']}")
                
                # Only check user messages for state transitions
                if message.role == "user":
                    await self._check_state_transition(message.content)
    
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
    
    async def _transition_to_state(self, new_state: str, reason: str):
        """
        Perform state transition and update LLM context.
        
        This uses the documented approach from Pipecat's context management:
        https://docs.pipecat.ai/guides/learn/context-management
        
        From the docs:
        - LLMMessagesAppendFrame: Appends a new message to the existing context
        - LLMMessagesUpdateFrame: Completely replaces the existing context with new messages
        
        We use LLMMessagesUpdateFrame to replace the system message while preserving
        conversation history (user/assistant messages).
        """
        
        if not self.task:
            logger.error("❌ Cannot transition: task not available yet")
            return
        
        old_state = self.conversation_context.current_state
        logger.info(f"🚀 [TRANSITION] {old_state} → {new_state} (reason: {reason})")
        
        # Update context state
        self.conversation_context.transition_to(new_state, reason=reason)
        
        # Render new prompt for the new state
        new_prompt = self.conversation_context.render_prompt()
        logger.info(f"📝 [PROMPT] New prompt length: {len(new_prompt)} chars")
        logger.info(f"📝 [PROMPT] Preview: {new_prompt[:200]}...")
        
        # Get current context (includes all user/assistant messages)
        # Reference: "The context aggregator provides a context property for getting the current context"
        current_context = self.context_aggregators.user().context
        current_messages = current_context.messages if current_context else []
        
        # Build new message array:
        # - New system message at the start
        # - Preserve all user/assistant conversation history
        new_messages = [{"role": "system", "content": new_prompt}]
        
        # Add all non-system messages from current context
        for msg in current_messages:
            if msg.get("role") != "system":
                new_messages.append(msg)
        
        logger.info(f"📝 [CONTEXT] Rebuilding context with {len(new_messages)} messages")
        logger.info(f"📝 [CONTEXT] Message roles: {[m['role'] for m in new_messages]}")
        
        try:
            # ✅ Use LLMMessagesUpdateFrame to replace entire context
            # This ensures the new system message is at the start, followed by conversation history
            await self.task.queue_frames([
                LLMMessagesUpdateFrame(
                    messages=new_messages,
                    run_llm=False  # Don't trigger immediate response, wait for next user input
                )
            ])
            logger.info(f"✅ State transition complete: {old_state} → {new_state}")
        except Exception as e:
            logger.error(f"❌ Failed to update context: {e}")
            logger.error(traceback.format_exc())
    
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
        
        @self.transport.event_handler("on_dialout_error")
        async def on_dialout_error(transport, data):
            logger.error(f"=== EVENT: on_dialout_error (Session: {self.session_id}) ===")
            logger.error(f"Dialout error: {json.dumps(data, indent=2)}")
            
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