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
from handlers import (
    setup_transcript_handler,
    setup_voicemail_handlers,
    setup_ivr_handlers,
    setup_dialout_handlers,
    setup_function_call_handler,
)
from core.state_manager import StateManager
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
from monitoring import emit_event, get_collector

import os
import sys
import json
import logging
import traceback
from typing import Dict, Any, Optional
from datetime import datetime
import asyncio
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
        self.state_manager = None
        
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

    async def _print_transcript_async(self):
        """Print transcript in background without blocking pipeline shutdown"""
        try:
            # Small delay to ensure all events are collected
            await asyncio.sleep(0.1)
            
            # Run synchronous print in executor to not block event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                get_collector().print_full_transcript,
                self.session_id
            )
            await loop.run_in_executor(
                None,
                get_collector().print_latency_waterfall,
                self.session_id
            )
        except Exception as e:
            logger.debug(f"Failed to print transcript: {e}")
    
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
        
        # STT
        stt_config = self.services_config['services']['stt']
        stt = DeepgramSTTService(
            api_key=stt_config['api_key'],
            model=stt_config['model'],
            options=LiveOptions(endpointing=stt_config['endpointing'])
        )
        
        # CREATE CONTEXT
        self.conversation_context = ConversationContext(
            schema=self.conversation_schema,
            patient_data=self.patient_data,
            session_id=self.session_id,
            prompt_renderer=self.prompt_renderer,
            data_formatter=self.data_formatter
        )

        self.state_manager = StateManager(
            conversation_context=self.conversation_context,
            schema=self.conversation_schema,
            session_id=self.session_id,
            patient_id=self.patient_id
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
        self.state_manager.set_context_aggregators(context_aggregators)

        # TTS
        tts_config = self.services_config['services']['tts']
        tts = ElevenLabsTTSService(
            api_key=tts_config['api_key'],
            voice_id=tts_config['voice_id'],
            model=tts_config['model'],
            stability=tts_config['stability']
        )
        
        # Setup event handlers
        self._setup_dialout_handlers()
        self._setup_transcript_handler()
        self._setup_voicemail_handlers(voicemail_detector)
        self._setup_ivr_handlers(ivr_navigator)
        self._setup_function_call_handler()
        
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
        setup_transcript_handler(self)

    def _setup_voicemail_handlers(self, voicemail_detector):
        """Setup VoicemailDetector event handlers"""
        setup_voicemail_handlers(self, voicemail_detector)

    def _setup_ivr_handlers(self, ivr_navigator):
        """Setup IVRNavigator event handlers"""
        setup_ivr_handlers(self, ivr_navigator)

    def _setup_dialout_handlers(self):
        """Setup Daily dial-out event handlers"""
        setup_dialout_handlers(self)

    def _setup_function_call_handler(self):
        """Setup handler for LLM function calls"""
        setup_function_call_handler(self)
    
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

        self.state_manager.set_task(self.task)
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
