
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
    """Custom runner handling Windows signal issues."""
    def _setup_sigint(self):
        if sys.platform == 'win32':
            return
        super()._setup_sigint()


class SchemaBasedPipeline:
    """Schema-driven voice AI pipeline with Daily.co telephony and state management."""
    
    def __init__(
        self, 
        session_id: str,
        patient_id: str,
        patient_data: Dict[str, Any],
        conversation_schema,
        data_formatter,
        prompt_renderer,
        phone_number: str,
        services_config: Dict[str, Any],
        debug_mode: bool = False
    ):
        self.session_id = session_id
        self.patient_id = patient_id
        self.patient_data = patient_data
        self.conversation_schema = conversation_schema
        self.data_formatter = data_formatter
        self.prompt_renderer = prompt_renderer
        self.phone_number = phone_number
        self.services_config = services_config
        
        # Pipeline components (initialized in create_pipeline)
        self.transport = None
        self.pipeline = None
        self.task = None
        self.llm = None
        self.conversation_context = None
        
        # Transcript tracking
        self.transcripts = []
        self.transcript_processor = TranscriptProcessor()
        
        emit_event(
            session_id=session_id,
            category="CALL",
            event="call_started",
            metadata={
                "patient_id": patient_id,
                "phone_number": phone_number,
                "schema_version": conversation_schema.conversation.version
            }
        )
    
    def create_pipeline(self, url: str, token: str, room_name: str):
        """Create the Pipecat pipeline with all services and handlers."""
        
        # VAD Analyzer
        vad_analyzer = SileroVADAnalyzer(params=VADParams(
            confidence=0.7, start_secs=0.2, stop_secs=0.8, min_volume=0.5
        ))
        
        # Transport
        transport_config = self.services_config['services']['transport']
        self.transport = DailyTransport(
            url, token, room_name,
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
        
        # STT
        stt_config = self.services_config['services']['stt']
        stt = DeepgramSTTService(
            api_key=stt_config['api_key'],
            model=stt_config['model'],
            options=LiveOptions(endpointing=stt_config['endpointing'])
        )
        
        # CREATE CONTEXT FIRST (so we can use prompt_renderer)
        self.conversation_context = ConversationContext(
            schema=self.conversation_schema,
            patient_data=self.patient_data,
            session_id=self.session_id,
            prompt_renderer=self.prompt_renderer,
            data_formatter=self.data_formatter
        )
        
        # Get formatted data for prompt rendering
        formatted_data = self.data_formatter.format_patient_data(self.patient_data)
        
        # Voicemail Detector with Classifier LLM
        classifier_config = self.services_config['services']['classifier_llm']
        classifier_llm = OpenAILLMService(
            api_key=classifier_config['api_key'],
            model=classifier_config['model'],
            temperature=classifier_config['temperature']
        )
        
        voicemail_prompt = self.conversation_context.prompt_renderer.render_prompt(
            "voicemail_detection", "system", formatted_data
        )
        
        voicemail_detector = VoicemailDetector(
            llm=classifier_llm,
            voicemail_response_delay=0.8,
            custom_system_prompt=voicemail_prompt
        )
        
        # Main LLM
        llm_config = self.services_config['services']['llm']
        main_llm = OpenAILLMService(
            api_key=llm_config['api_key'],
            model=llm_config['model'],
            temperature=llm_config['temperature']
        )
        main_llm.register_function("update_prior_auth_status", update_prior_auth_status_handler)
        self.llm = main_llm
        
        # IVR Navigator
        ivr_goal = self.conversation_context.prompt_renderer.render_prompt(
            "ivr_navigation", "task", formatted_data
        ) or "Navigate to provider services for eligibility verification"
        
        ivr_navigator = IVRNavigator(
            llm=main_llm,
            ivr_prompt=ivr_goal,
            ivr_vad_params=VADParams(stop_secs=2.0)
        )
        
        # Context Aggregator
        initial_prompt = self.conversation_context.render_prompt()
        llm_context = OpenAILLMContext(
            messages=[{"role": "system", "content": initial_prompt}],
            tools=PATIENT_TOOLS
        )
        context_aggregators = self.llm.create_context_aggregator(llm_context)
        self.context_aggregators = context_aggregators
        
        # TTS
        tts_config = self.services_config['services']['tts']
        tts = ElevenLabsTTSService(
            api_key=tts_config['api_key'],
            voice_id=tts_config['voice_id'],
            model=tts_config['model'],
            stability=tts_config['stability']
        )
        
        # Setup event handlers
        self._setup_transcript_handler()
        self._setup_voicemail_handlers(voicemail_detector)
        self._setup_ivr_handlers(ivr_navigator)
        
        # Build Pipeline
        self.pipeline = Pipeline([
            self.transport.input(),
            AudioResampler(target_sample_rate=16000),
            DropEmptyAudio(),
            stt,
            voicemail_detector.detector(),
            self.transcript_processor.user(),
            context_aggregators.user(),
            ivr_navigator,
            StateTagStripper(),
            tts,
            voicemail_detector.gate(),
            self.transcript_processor.assistant(),
            context_aggregators.assistant(),
            self.transport.output()
        ])

        logger.info("✅ Pipeline created successfully")

    def _setup_transcript_handler(self):
        """Monitor transcripts for state transitions and completion"""
        
        @self.transcript_processor.event_handler("on_transcript_update")
        async def handle_transcript_update(processor, frame):
            for message in frame.messages:
                transcript_entry = {
                    "role": message.role,
                    "content": message.content,
                    "timestamp": message.timestamp or datetime.now().isoformat(),
                    "type": "transcript"
                }
                self.transcripts.append(transcript_entry)
                logger.info(f"[TRANSCRIPT] {message.role}: {message.content}")
                
                if message.role == "user":
                    await self._check_state_transition(message.content)
                elif message.role == "assistant":
                    await self._check_assistant_state_transition(message.content)
            
            await self._check_for_call_completion()

    def _setup_voicemail_handlers(self, voicemail_detector):
        """Setup VoicemailDetector event handlers"""
        self.voicemail_detector = voicemail_detector
        
        @self.voicemail_detector.event_handler("on_voicemail_detected")
        async def handle_voicemail(processor):
            logger.info(f"📞 Voicemail detected - Session: {self.session_id}")
            
            emit_event(
                session_id=self.session_id,
                category="DETECTION",
                event="voicemail_detected",
                metadata={"phone_number": self.phone_number}
            )
            
            # Transition to voicemail state
            await self._transition_to_state("voicemail_detected", "voicemail_system_detected")
            
            # Get voicemail message from schema
            voicemail_prompt = self.conversation_schema.prompts.get("voicemail_detected", {})
            message = voicemail_prompt.get("message", "")
            
            if message:
                message = self.prompt_renderer.render_template(message, self.patient_data)
            else:
                message = "Hello, this was Alexandra trying to reach out regarding eligibility and benefits verification. Thank you."

            await processor.push_frame(TTSSpeakFrame(message))
            await get_async_patient_db().update_call_status(self.patient_id, "Completed - Left VM")
            
            if self.task:
                await self.task.queue_frames([EndFrame()])
            
            logger.info("✅ Voicemail left, call ending")
            
    def _setup_ivr_handlers(self, ivr_navigator):
        """Setup IVRNavigator event handlers"""
        self.ivr_navigator = ivr_navigator
        
        @self.ivr_navigator.event_handler("on_conversation_detected")
        async def on_conversation_detected(processor, conversation_history):
            logger.info(f"👤 Human answered - Session: {self.session_id}")
            
            emit_event(
                session_id=self.session_id,
                category="DETECTION",
                event="conversation_detected",
                metadata={"phone_number": self.phone_number}
            )
            
            self.conversation_context.transition_to("greeting", "human_answered_directly")
            greeting_prompt = self.conversation_context.render_prompt()
            messages = [{"role": "system", "content": greeting_prompt}]
            
            if conversation_history:
                messages.extend(conversation_history)
            
            # Start conversation
            if self.task:
                await self.task.queue_frames([
                    LLMMessagesUpdateFrame(messages=messages, run_llm=True),
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                ])
            
            logger.info("✅ Conversation started")
        
        @self.ivr_navigator.event_handler("on_ivr_status_changed")
        async def on_ivr_status_changed(processor, status):
            logger.info(f"🤖 IVR Status: {status}")
            
            emit_event(
                session_id=self.session_id,
                category="DETECTION",
                event="ivr_status_changed",
                metadata={"status": str(status), "phone_number": self.phone_number}
            )
            
            if status == IVRStatus.DETECTED:
                self.conversation_context.transition_to("ivr_navigation", "ivr_system_detected")
                
                ivr_prompt = self.conversation_context.render_prompt()
                if self.task:
                    await self.task.queue_frames([
                        LLMMessagesUpdateFrame(
                            messages=[{"role": "system", "content": ivr_prompt}],
                            run_llm=False
                        )
                    ])
                
                logger.info("✅ IVR navigation started")
            
            elif status == IVRStatus.COMPLETED:
                self.conversation_context.transition_to("greeting", "ivr_navigation_complete")
                
                greeting_prompt = self.conversation_context.render_prompt()
                messages = [{"role": "system", "content": greeting_prompt}]
                
                if self.task:
                    await self.task.queue_frames([
                        LLMMessagesUpdateFrame(messages=messages, run_llm=True),
                        VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                    ])
                
                logger.info("✅ IVR completed, conversation started")
            
            elif status == IVRStatus.STUCK:
                logger.warning("⚠️ IVR navigation stuck - ending call")
                
                self.conversation_context.transition_to("ivr_stuck", "ivr_navigation_failed")
                prompts_dict = self.conversation_schema.prompts.get("prompts", {})
                ivr_stuck_config = prompts_dict.get("ivr_stuck", {})
                message = ivr_stuck_config.get("message", "")
                
                if message:
                    message = self.prompt_renderer.render_template(message, self.patient_data)
                else:
                    message = "I apologize, but I'm unable to navigate to the appropriate department. We will try again later."
                
                if self.task:
                    await self.task.queue_frames([
                        TTSSpeakFrame(message),
                        EndFrame()
                    ])
                
                await get_async_patient_db().update_call_status(self.patient_id, "Failed")
                logger.info("❌ Call ended - IVR stuck")
                
    def _setup_function_call_handler(self):
        """Setup handler for LLM function calls"""
        
        @self.llm.event_handler("on_function_call")
        async def handle_function_call(llm, function_name, arguments):
            logger.info(f"🔧 Function call: {function_name}")
            
            func = FUNCTION_REGISTRY.get(function_name)
            if not func:
                logger.error(f"Function not found: {function_name}")
                return {"error": f"Function {function_name} not found"}
            
            # Add patient_id if missing
            if "patient_id" not in arguments:
                arguments["patient_id"] = self.patient_id
            
            try:
                result = await func(**arguments)
                
                emit_event(
                    session_id=self.session_id,
                    category="FUNCTION",
                    event="function_executed",
                    metadata={
                        "function_name": function_name,
                        "result": result
                    }
                )
                
                return {"success": result}
                
            except Exception as e:
                logger.error(f"Function error ({function_name}): {e}")
                return {"error": str(e)}
    
    async def _check_state_transition(self, user_message: str):
        """Check if user message triggers a state transition based on schema rules."""
        
        current_state = self.conversation_context.current_state
        transition = self.conversation_schema.check_transition(current_state, user_message)
        
        if transition:
            logger.info(f"🎯 Transition: {transition.from_state} → {transition.to_state} ({transition.trigger.type})")
            await self._transition_to_state(transition.to_state, transition.reason)


    async def _check_assistant_state_transition(self, assistant_message: str):
        """Parse assistant response for LLM-directed state transitions."""
        import re
        
        # Extract <next_state> tag
        match = re.search(r'<next_state>(\w+)</next_state>', assistant_message, re.IGNORECASE)
        if not match:
            return
        
        requested_state = match.group(1).lower()
        current_state = self.conversation_context.current_state
        if not self.conversation_schema.is_llm_directed(current_state):
            return
        allowed_transitions = self.conversation_schema.get_allowed_transitions(current_state)
        if requested_state in allowed_transitions:
            logger.info(f"🤖 LLM transition: {current_state} → {requested_state}")
            await self._transition_to_state(requested_state, "llm_directed")
            
            emit_event(
                session_id=self.session_id,
                category="STATE",
                event="llm_directed_transition",
                metadata={
                    "from_state": current_state,
                    "to_state": requested_state
                }
            )
        else:
            logger.warning(f"⚠️ LLM transition blocked: {requested_state} not in {allowed_transitions}")
            
            emit_event(
                session_id=self.session_id,
                category="STATE",
                event="llm_transition_blocked",
                severity="warning",
                metadata={
                    "from_state": current_state,
                    "requested_state": requested_state,
                    "allowed_transitions": allowed_transitions
                }
            )

    async def _check_for_call_completion(self):
        """Terminate pipeline if in closing state and goodbye said."""
        if self.conversation_context.current_state != "closing":
            return
        
        assistant_messages = [t for t in self.transcripts if t["role"] == "assistant"]
        if not assistant_messages:
            return
        
        last_msg = assistant_messages[-1]["content"].lower()
        goodbye_phrases = ["goodbye", "have a great day", "thank you"]
        
        if any(phrase in last_msg for phrase in goodbye_phrases):
            logger.info("🏁 Call complete - terminating")
            
            await get_async_patient_db().update_call_status(self.patient_id, "Completed")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="call_completed",
                metadata={"patient_id": self.patient_id}
            )
            
            if self.task:
                await self.task.queue_frames([EndFrame()])
    
    async def _transition_to_state(self, new_state: str, reason: str):
        """Perform state transition and update LLM context."""
        
        if not self.task:
            logger.error(f"Cannot transition: task not available ({self.conversation_context.current_state} → {new_state})")
            return
        
        old_state = self.conversation_context.current_state
        logger.info(f"🔄 {old_state} → {new_state} ({reason})")
        if new_state in ["connection", "voicemail_detected", "ivr_stuck"]:
            self.conversation_context.transition_to(new_state, reason=reason)
            return

        self.conversation_context.transition_to(new_state, reason=reason)
        new_prompt = self.conversation_context.render_prompt()
        if new_state == "verification":
            new_prompt += f"\n\nIMPORTANT: The patient_id for function calls is: {self.patient_id}"

        current_context = self.context_aggregators.user().context
        current_messages = current_context.messages if current_context else []

        new_messages = [{"role": "system", "content": new_prompt}]
        new_messages.extend([msg for msg in current_messages if msg.get("role") != "system"])

        await self.task.queue_frames([
            LLMMessagesUpdateFrame(messages=new_messages, run_llm=False)
        ])
        
        logger.info(f"✅ Transitioned to {new_state}")
            
    def _setup_dialout_handlers(self):
        """Setup Daily dial-out event handlers"""
        
        @self.transport.event_handler("on_joined")
        async def on_joined(transport, data):
            logger.info(f"Bot joined, dialing {self.phone_number}")
            
            try:
                await transport.start_dialout({"phoneNumber": self.phone_number})
                emit_event(
                    session_id=self.session_id,
                    category="CALL",
                    event="dialout_initiated",
                    metadata={"phone_number": self.phone_number}
                )
            except Exception as e:
                logger.error(f"Dial-out failed: {e}")
                emit_event(
                    session_id=self.session_id,
                    category="CALL",
                    event="dialout_failed",
                    severity="error",
                    metadata={"phone_number": self.phone_number, "error": str(e)}
                )
        
        @self.transport.event_handler("on_dialout_answered")
        async def on_dialout_answered(transport, data):
            logger.info(f"Call answered: {self.phone_number}")
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="dialout_answered",
                metadata={"phone_number": self.phone_number}
            )
        
        @self.transport.event_handler("on_dialout_stopped")
        async def on_dialout_stopped(transport, data):
            logger.info("Call ended")
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="dialout_stopped",
                metadata={"phone_number": self.phone_number}
            )
        
        @self.transport.event_handler("on_participant_left")
        async def on_participant_left(transport, participant, data):
            logger.info(f"Participant left: {participant}")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="participant_left",
                metadata={"participant_id": participant}
            )
            
            # Update call status if not already terminal
            try:
                patient = await get_async_patient_db().find_patient_by_id(self.patient_id)
                current_status = patient.get("call_status") if patient else None
                
                if current_status not in ["Completed", "Completed - Left VM", "Failed"]:
                    await get_async_patient_db().update_call_status(self.patient_id, "Completed")
                    logger.info("✅ Call status: Completed")
            except Exception as e:
                logger.error(f"Error updating call status: {e}")
            
            # Terminate pipeline
            if self.task:
                await self.task.cancel()
        
        @self.transport.event_handler("on_dialout_error")
        async def on_dialout_error(transport, data):
            logger.error(f"Dialout error: {data}")
            
            await get_async_patient_db().update_call_status(self.patient_id, "Failed")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="dialout_error",
                severity="error",
                metadata={"phone_number": self.phone_number, "error_data": data}
            )
    
    async def run(self, url: str, token: str, room_name: str):
        """Run the schema-based pipeline"""
        logger.info(f"Starting pipeline - Session: {self.session_id}, Phone: {self.phone_number}")
        
        if not self.pipeline:
            self.create_pipeline(url, token, room_name)
        
        self.task = PipelineTask(
            self.pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
            ),
            conversation_id=self.session_id
        )
        
        self.runner = CustomPipelineRunner()
        
        try:
            await self.runner.run(self.task)
            logger.info("Pipeline completed")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="call_ended",
                metadata={"status": "completed"}
            )
            
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="call_failed",
                severity="error",
                metadata={"error": str(e)}
            )
            
            raise


    def get_conversation_state(self) -> Dict[str, Any]:
        """Get current conversation state"""
        if not self.conversation_context:
            return {
                "workflow_state": "inactive",
                "patient_data": self.patient_data,
                "phone_number": self.phone_number,
                "transcripts": []
            }
        
        return {
            "workflow_state": "active",
            "current_state": self.conversation_context.current_state,
            "patient_data": self.patient_data,
            "phone_number": self.phone_number,
            "transcripts": self.transcripts,
        }