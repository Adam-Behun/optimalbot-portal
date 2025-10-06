from pipecat.frames.frames import LLMMessagesFrame, AudioRawFrame, Frame, TextFrame, TTSAudioRawFrame
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
from pipecat_flows import FlowManager
from deepgram import LiveOptions

# Local imports
from audio_processors import AudioResampler, DropEmptyAudio
from flow_nodes import create_greeting_node
from functions import PATIENT_FUNCTIONS

import os
import sys
import json
import logging
import traceback
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

def setup_logging(level=logging.INFO):
    """Configure logging for the pipeline"""
    logger = logging.getLogger(__name__)
    logger.setLevel(level)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger

logger = setup_logging(logging.DEBUG if os.getenv("DEBUG") else logging.INFO)

class CustomPipelineRunner(PipelineRunner):
    def _setup_sigint(self):
        if sys.platform == 'win32':
            logger.warning("Signal handling not supported on Windows")
            return
        super()._setup_sigint()

class HealthcareAIPipeline:
    def __init__(self, session_id: str = "default", patient_id: str = None, 
                 patient_data: Optional[Dict[str, Any]] = None, debug_mode: bool = False):
        self.transport = None
        self.pipeline = None
        self.runner = None
        self.session_id = session_id
        self.patient_id = patient_id
        self.patient_data = patient_data
        self.transcripts = []
        self.transcript_processor = TranscriptProcessor()
        self.flow_manager = None
        self.debug_mode = debug_mode
        self.llm = None
        self.context_aggregators = None
        
        logger.info(f"=== PIPELINE INITIALIZATION ===")
        logger.info(f"Session ID: {session_id}")
        logger.info(f"Patient ID: {patient_id}")
        logger.info(f"Patient Name: {patient_data.get('patient_name') if patient_data else 'None'}")
        logger.info(f"Debug Mode: {debug_mode}")
        
    def create_pipeline(self, url: str, token: str, room_name: str) -> Pipeline:
        """Create the pipeline without dialout_settings (dialout triggered via REST API)"""
        logger.info(f"=== CREATING PIPELINE ===")
        logger.info(f"Room Name: {room_name}")
        logger.info(f"Room URL: {url}")
        logger.info(f"Token present: {bool(token)}")
        
        # DailyParams - dialout_settings removed as it's not a valid parameter
        params = DailyParams(
            api_key=os.getenv("DAILY_API_KEY"),
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5))
        )
        
        logger.info("DailyParams created successfully")
        logger.info(f"  - audio_in_enabled: True")
        logger.info(f"  - audio_out_enabled: True")
        logger.info(f"  - vad_enabled: True")
        
        # Create DailyTransport
        logger.info("Creating DailyTransport...")
        self.transport = DailyTransport(url, token, "Healthcare AI Bot", params)
        logger.info("DailyTransport created successfully")
        
        # Always setup dial-out event handlers for monitoring
        self._setup_dialout_handlers()
        
        logger.info("=== INITIALIZING SERVICES ===")
        
        # Deepgram STT
        logger.info("Creating Deepgram STT Service...")
        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            live_options=LiveOptions(
                model="nova-2",
                language="en-US",
                encoding="linear16",
                sample_rate=16000,  # Daily PSTN uses 16kHz
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
        logger.info("Deepgram STT Service created")
        
        # OpenAI LLM
        logger.info("Creating OpenAI LLM Service...")
        self.llm = OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY"),
            model="gpt-4o-mini",
            tools=PATIENT_FUNCTIONS
        )
        logger.info(f"OpenAI LLM Service created with {len(PATIENT_FUNCTIONS)} functions")

        self._setup_transcript_handler()

        llm_context = OpenAILLMContext(messages=[])
        self.context_aggregators = self.llm.create_context_aggregator(llm_context)
        logger.info("LLM context aggregators created")
        
        # ElevenLabs TTS
        logger.info("Creating ElevenLabs TTS Service...")
        tts = ElevenLabsTTSService(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            voice_id="FGY2WhTYpPnrIDTdsKH5",
            model="eleven_turbo_v2_5"
        )
        logger.info("ElevenLabs TTS Service created")

        # Build pipeline
        logger.info("=== BUILDING PIPELINE ===")
        pipeline_components = [
            self.transport.input(),
            AudioResampler(),
            DropEmptyAudio(),
            stt,
            self.transcript_processor.user(),
            self.context_aggregators.user(),
            self.llm,
            tts,
            self.transcript_processor.assistant(),
            self.context_aggregators.assistant(),
            self.transport.output()
        ]
        
        logger.info(f"Pipeline has {len(pipeline_components)} components:")
        for i, component in enumerate(pipeline_components):
            logger.info(f"  {i+1}. {type(component).__name__}")

        self.pipeline = Pipeline(pipeline_components)
        logger.info("✓ Healthcare pipeline created successfully")
        return self.pipeline
        

    def _setup_dialout_handlers(self):
        """Setup handlers for Daily dial-out events (dialout triggered via REST API)"""
        logger.info("Setting up dialout event handlers...")
        
        @self.transport.event_handler("on_joined")
        async def on_joined(transport, data):
            logger.info(f"=== EVENT: on_joined ===")
            logger.info(f"Session: {self.session_id}")
            logger.info(f"Join data: {data}")
        
        @self.transport.event_handler("on_dialout_answered")
        async def on_dialout_answered(transport, data):
            logger.info(f"=== EVENT: on_dialout_answered ===")
            logger.info(f"✓ Call answered for session {self.session_id}")
            logger.info(f"Dialout answered data: {json.dumps(data, indent=2)}")
        
        @self.transport.event_handler("on_dialout_stopped")
        async def on_dialout_stopped(transport, data):
            logger.info(f"=== EVENT: on_dialout_stopped ===")
            logger.info(f"Call ended for session {self.session_id}")
            logger.info(f"Dialout stopped data: {json.dumps(data, indent=2)}")
        
        @self.transport.event_handler("on_dialout_error")
        async def on_dialout_error(transport, data):
            logger.error(f"=== EVENT: on_dialout_error ===")
            logger.error(f"Dial-out error for session {self.session_id}")
            logger.error(f"Error data: {json.dumps(data, indent=2)}")
        
        @self.transport.event_handler("on_dialout_warning")
        async def on_dialout_warning(transport, data):
            logger.warning(f"=== EVENT: on_dialout_warning ===")
            logger.warning(f"Dial-out warning for session {self.session_id}")
            logger.warning(f"Warning data: {json.dumps(data, indent=2)}")
        
        @self.transport.event_handler("on_participant_joined")
        async def on_participant_joined(transport, data):
            logger.info(f"=== EVENT: on_participant_joined ===")
            logger.info(f"Participant joined: {json.dumps(data, indent=2)}")
        
        @self.transport.event_handler("on_participant_left")
        async def on_participant_left(transport, data):
            logger.info(f"=== EVENT: on_participant_left ===")
            logger.info(f"Participant left: {json.dumps(data, indent=2)}")
        
        @self.transport.event_handler("on_call_state_updated")
        async def on_call_state_updated(transport, state):
            logger.info(f"=== EVENT: on_call_state_updated ===")
            logger.info(f"Call state: {state}")
        
        logger.info("✓ Dial-out event handlers registered")

    def _setup_transcript_handler(self):
        """Setup transcript event handler"""
        logger.info("Setting up transcript handler...")
        
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
                logger.info(f"=== TRANSCRIPT ===")
                logger.info(f"[{transcript_entry['timestamp']}] {transcript_entry['role']}: {transcript_entry['content']}")
        
        logger.info("✓ Transcript handler registered")
    
    async def run(self, url: str, token: str, room_name: str):
        """Run the healthcare pipeline (dialout triggered via REST API in app.py)"""
        logger.info(f"=== RUNNING PIPELINE ===")
        logger.info(f"Session: {self.session_id}")
        
        if not self.pipeline:
            logger.info("Pipeline not created yet, creating now...")
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
        logger.info("PipelineTask created")
        
        # Create FlowManager
        logger.info("=== CREATING FLOW MANAGER ===")
        self.flow_manager = FlowManager(
            task=task,
            llm=self.llm,
            context_aggregator=self.context_aggregators
        )
        logger.info("FlowManager created")
        
        # Initialize flow with patient data
        if self.flow_manager and self.patient_data:
            logger.info("=== INITIALIZING FLOW WITH PATIENT DATA ===")
            logger.info(f"Patient: {self.patient_data.get('patient_name')}")
            logger.info(f"Insurance: {self.patient_data.get('insurance_company_name')}")
            logger.info(f"Facility: {self.patient_data.get('facility_name')}")
            
            self.flow_manager.state["patient_data"] = self.patient_data
            self.flow_manager.state["patient_id"] = self.patient_data.get('_id')
            self.flow_manager.state["collected_info"] = {
                "reference_number": None,
                "auth_status": None,
                "insurance_rep_name": None
            }
            logger.info("Flow state initialized")
            
            logger.info("Creating greeting node...")
            greeting_node = create_greeting_node(self.patient_data)
            logger.info(f"Greeting node created: {greeting_node}")
            
            logger.info("Initializing flow with greeting node...")
            await self.flow_manager.initialize(greeting_node)
            
            logger.info(f"✓ Flow initialized with patient data for: {self.patient_data.get('patient_name')}")
        else:
            logger.warning("No patient data available for flow initialization")
        
        logger.info("Creating CustomPipelineRunner...")
        self.runner = CustomPipelineRunner()
        logger.info(f"✓ Starting healthcare pipeline for session: {self.session_id}")
        
        try:
            logger.info("=== RUNNING PIPELINE TASK ===")
            await self.runner.run(task)
            logger.info("Pipeline task completed")
        except Exception as e:
            logger.error(f"=== PIPELINE ERROR ===")
            logger.error(f"Error: {str(e)}")
            logger.error(f"Traceback:\n{traceback.format_exc()}")
            raise

    def get_conversation_state(self):
        """Get current conversation state"""
        logger.debug("Getting conversation state...")
        
        if self.flow_manager and hasattr(self.flow_manager, 'state'):
            state = {
                "workflow_state": "active",
                "patient_data": self.flow_manager.state.get("patient_data", self.patient_data),
                "collected_info": self.flow_manager.state.get("collected_info", {})
            }
            logger.debug(f"Conversation state: {json.dumps(state, indent=2, default=str)}")
            return state
        
        logger.debug("Flow manager not active, returning inactive state")
        return {
            "workflow_state": "inactive",
            "patient_data": self.patient_data,
            "collected_info": {}
        }