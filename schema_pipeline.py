"""
Schema-Based Pipeline - Voice AI pipeline using schema-driven prompts and state management.
Replaces FlowManager with ConversationContext for schema-driven conversation flow.
"""

from pipecat.frames.frames import Frame, TextFrame, LLMMessagesFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
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
from functions import PATIENT_FUNCTIONS

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


class SchemaLLMProcessor(FrameProcessor):
    """
    Custom LLM processor that uses ConversationContext for schema-driven prompts.
    Replaces FlowManager with lightweight schema-based state management.
    """
    
    def __init__(self, context: ConversationContext, llm: OpenAILLMService, **kwargs):
        super().__init__(**kwargs)
        self.context = context
        self.llm = llm
        logger.info("SchemaLLMProcessor initialized")
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames and inject schema-driven prompts"""
        await super().process_frame(frame, direction)
        
        # Pass through all frames (additional schema injections can be added here for dynamic updates)
        await self.push_frame(frame, direction)


class SchemaBasedPipeline:
    """
    Schema-driven voice AI pipeline using Daily.co telephony.
    Replaces HealthcareAIPipeline with schema-based architecture.
    """
    
    def __init__(
        self, 
        session_id: str,
        patient_id: str,
        patient_data: Dict[str, Any],
        conversation_schema,
        data_formatter,
        debug_mode: bool = False
    ):
        """
        Initialize pipeline with schema system components.
        
        Args:
            session_id: Unique session identifier
            patient_id: Patient database ID
            patient_data: Raw patient data from database
            conversation_schema: Global ConversationSchema instance
            data_formatter: Global DataFormatter instance
            debug_mode: Enable debug logging
        """
        self.session_id = session_id
        self.patient_id = patient_id
        self.patient_data = patient_data
        self.conversation_schema = conversation_schema
        self.data_formatter = data_formatter
        self.debug_mode = debug_mode
        
        # Pipeline components (created in create_pipeline)
        self.transport = None
        self.pipeline = None
        self.runner = None
        self.llm = None
        self.context_aggregators = None
        self.conversation_context = None
        
        # Transcript tracking
        self.transcripts = []
        self.transcript_processor = TranscriptProcessor()
        
        logger.info(f"=== SCHEMA PIPELINE INITIALIZATION ===")
        logger.info(f"Session ID: {session_id}")
        logger.info(f"Patient ID: {patient_id}")
        logger.info(f"Patient: {patient_data.get('patient_name')}")
        logger.info(f"Schema: {conversation_schema.conversation.name} v{conversation_schema.conversation.version}")
    
    def create_pipeline(self, url: str, token: str, room_name: str) -> Pipeline:
        """
        Create pipecat pipeline with schema-driven conversation flow.
        """
        logger.info(f"=== CREATING SCHEMA PIPELINE ===")
        logger.info(f"Room: {room_name}")
        
        # Daily transport configuration
        params = DailyParams(
            api_key=os.getenv("DAILY_API_KEY"),
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5))
        )
        
        logger.info("Creating DailyTransport...")
        self.transport = DailyTransport(url, token, "Healthcare AI Bot", params)
        self._setup_dialout_handlers()
        
        logger.info("=== INITIALIZING SERVICES ===")
        
        # Deepgram STT
        logger.info("Creating Deepgram STT...")
        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            live_options=LiveOptions(
                model="nova-2",
                language="en-US",
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                interim_results=True,
                endpointing=200,
                vad_events=True,
                smart_format=True,
                punctuate=True,
                filler_words=True,
                utterance_end_ms=1000,
            )
        )
        
        # OpenAI LLM
        logger.info("Creating OpenAI LLM...")
        self.llm = OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY"),
            model="gpt-4o-mini",
            tools=PATIENT_FUNCTIONS
        )
        
        # Create ConversationContext with pre-formatted data
        logger.info("=== INITIALIZING CONVERSATION CONTEXT ===")
        formatted_data = self.data_formatter.format_patient_data(self.patient_data)
        self.conversation_context = ConversationContext(
            schema=self.conversation_schema,
            patient_data=formatted_data,
            session_id=self.session_id
        )
        logger.info(f"Context initialized - Initial state: {self.conversation_context.current_state}")
        
        # LLM context aggregator
        initial_prompt = self.conversation_context.render_prompt()  # From YAML
        llm_context = OpenAILLMContext(messages=[{"role": "system", "content": initial_prompt}])
        self.context_aggregators = self.llm.create_context_aggregator(llm_context)
        
        # ElevenLabs TTS
        logger.info("Creating ElevenLabs TTS...")
        tts = ElevenLabsTTSService(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            voice_id="FGY2WhTYpPnrIDTdsKH5",  # Or slower voice like "s5R3aTt9gdWzBWhP"
            model="eleven_multilingual_v2",  # Non-turbo for better enunciation
            stability=0.75,  # Higher for consistent pacing
            clarity=1.0  # Emphasize clarity
        )
        
        # Setup transcript handler
        self._setup_transcript_handler()
        
        # Create custom schema-driven LLM processor
        schema_llm_processor = SchemaLLMProcessor(
            context=self.conversation_context,
            llm=self.llm
        )
        
        # Build pipeline
        logger.info("=== BUILDING PIPELINE ===")
        pipeline_components = [
            self.transport.input(),
            AudioResampler(),
            DropEmptyAudio(),
            stt,
            self.transcript_processor.user(),
            self.context_aggregators.user(),
            schema_llm_processor,  # Our custom schema processor
            self.llm,
            tts,
            self.transcript_processor.assistant(),
            self.context_aggregators.assistant(),
            self.transport.output()
        ]
        
        logger.info(f"Pipeline components ({len(pipeline_components)}):")
        for i, component in enumerate(pipeline_components, 1):
            logger.info(f"  {i}. {type(component).__name__}")
        
        self.pipeline = Pipeline(pipeline_components)
        logger.info("✅ Schema pipeline created successfully")
        return self.pipeline
    
    def _setup_dialout_handlers(self):
        """Setup Daily dial-out event handlers"""
        logger.info("Setting up dialout event handlers...")
        
        @self.transport.event_handler("on_joined")
        async def on_joined(transport, data):
            logger.info(f"=== EVENT: on_joined (Session: {self.session_id}) ===")
        
        @self.transport.event_handler("on_dialout_answered")
        async def on_dialout_answered(transport, data):
            logger.info(f"=== EVENT: on_dialout_answered (Session: {self.session_id}) ===")
            logger.info(f"✅ Call answered")
        
        @self.transport.event_handler("on_dialout_stopped")
        async def on_dialout_stopped(transport, data):
            logger.info(f"=== EVENT: on_dialout_stopped (Session: {self.session_id}) ===")
        
        @self.transport.event_handler("on_dialout_error")
        async def on_dialout_error(transport, data):
            logger.error(f"=== EVENT: on_dialout_error (Session: {self.session_id}) ===")
            logger.error(f"Error: {json.dumps(data, indent=2)}")
        
        logger.info("✅ Dialout handlers registered")
    
    def _setup_transcript_handler(self):
        """Setup transcript event handler"""
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
                logger.info(f"[TRANSCRIPT] {transcript_entry['role']}: {transcript_entry['content']}")
    
    async def run(self, url: str, token: str, room_name: str):
        """Run the schema-based pipeline"""
        logger.info(f"=== RUNNING SCHEMA PIPELINE ===")
        logger.info(f"Session: {self.session_id}")
        
        if not self.pipeline:
            logger.info("Creating pipeline...")
            self.create_pipeline(url, token, room_name)
        
        logger.info("Creating PipelineTask...")
        task = PipelineTask(
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
            await self.runner.run(task)
            logger.info("Pipeline completed successfully")
        except Exception as e:
            logger.error(f"=== PIPELINE ERROR ===")
            logger.error(f"Error: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
    def get_conversation_state(self) -> Dict[str, Any]:
        """Get current conversation state"""
        if self.conversation_context:
            return {
                "workflow_state": "active",
                "current_state": self.conversation_context.current_state,
                "state_history": self.conversation_context.state_history,
                "patient_data": self.patient_data,
                "transcripts": self.transcripts
            }
        
        return {
            "workflow_state": "inactive",
            "patient_data": self.patient_data,
            "transcripts": []
        }