"""
Conversation pipeline runner.
Orchestrates loading client config, building pipeline, and running calls.
"""

import sys
import logging
from typing import Dict, Any

from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams

from core.client_loader import ClientLoader
from pipeline.pipeline_factory import PipelineFactory
from handlers import (
    setup_dialout_handlers,
    setup_transcript_handler,
    setup_ivr_handlers,
    setup_function_call_handler,
)

logger = logging.getLogger(__name__)


class CustomPipelineRunner(PipelineRunner):
    """Custom runner handling Windows signal issues"""
    def _setup_sigint(self):
        if sys.platform == 'win32':
            return
        super()._setup_sigint()


class ConversationPipeline:
    """
    Schema-driven voice AI pipeline orchestrator.
    
    Handles:
    - Loading client configuration
    - Building Pipecat pipeline
    - Running voice conversations
    - Managing handlers and state
    """
    
    def __init__(
        self,
        client_name: str,
        session_id: str,
        patient_id: str,
        patient_data: Dict[str, Any],
        phone_number: str,
        debug_mode: bool = False
    ):
        self.client_name = client_name
        self.session_id = session_id
        self.patient_id = patient_id
        self.patient_data = patient_data
        self.phone_number = phone_number
        self.debug_mode = debug_mode
        
        # Load client configuration
        loader = ClientLoader(client_name)
        self.client_config = loader.load_all()
        self.conversation_schema = self.client_config.schema
        
        # Pipeline components (initialized in run())
        self.pipeline = None
        self.transport = None
        self.task = None
        self.conversation_context = None
        self.state_manager = None
        self.context_aggregators = None
        self.transcript_processor = None
        self.ivr_navigator = None
        self.llm = None
        self.runner = None
        
        # Transcript tracking
        self.transcripts = []

        # Transfer state
        self.transfer_in_progress = False

    async def run(self, room_url: str, room_token: str, room_name: str):
        logger.info(
            f"Starting pipeline - Client: {self.client_name}, "
            f"Session: {self.session_id}, Phone: {self.phone_number}"
        )

        # Build pipeline
        session_data = {
            'session_id': self.session_id,
            'patient_id': self.patient_id,
            'patient_data': self.patient_data,
            'phone_number': self.phone_number
        }

        room_config = {
            'room_url': room_url,
            'room_token': room_token,
            'room_name': room_name
        }

        logger.info(f"📋 Building pipeline with session_data keys: {list(session_data.keys())}")
        logger.info(f"📋 Room config: {room_name}")
        logger.info("📋 Calling PipelineFactory.build()...")

        self.pipeline, self.transport, components = PipelineFactory.build(
            self.client_config,
            session_data,
            room_config
        )

        logger.info("✅ Pipeline built successfully")
        logger.info(f"✅ Pipeline object: {type(self.pipeline).__name__}")
        logger.info(f"✅ Transport object: {type(self.transport).__name__}")

        # Extract components
        self.conversation_context = components['context']
        self.state_manager = components['state_manager']
        self.transcript_processor = components['transcript_processor']
        self.context_aggregators = components['context_aggregators']
        self.ivr_navigator = components['ivr_navigator']
        self.llm = components['llm']

        logger.info(f"✅ Components extracted - State: {self.conversation_context.current_state}")
        logger.info(f"✅ LLM service: {type(self.llm).__name__}")
        logger.info(f"✅ Transport: {type(self.transport).__name__}")

        # CRITICAL: Setup handlers BEFORE creating task
        # The task will join the Daily room immediately, so handlers must be ready
        logger.info("🔧 Setting up dialout handlers...")
        setup_dialout_handlers(self)

        logger.info("🔧 Setting up transcript handler...")
        setup_transcript_handler(self)

        logger.info("🔧 Setting up IVR handlers...")
        setup_ivr_handlers(self, components['ivr_navigator'])

        logger.info("🔧 Setting up function call handler...")
        setup_function_call_handler(self)

        logger.info("✅ All handlers configured")

        # Create task with OpenTelemetry enabled
        # NOTE: Task will trigger transport.join() immediately
        # Pipecat will automatically create the conversation span and all child spans
        self.task = PipelineTask(
            self.pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                enable_usage_metrics=True,  # ✅ ADDED: Required for token tracking
            ),
            enable_tracing=True,  # Enable tracing for this task
            enable_turn_tracking=True,  # Enable turn tracking
            conversation_id=self.session_id,  # Use session_id as conversation_id
            additional_span_attributes={
                "patient.id": self.patient_id,
                "phone.number": self.phone_number,
                "client.name": self.client_name,
            }
        )
        
        self.state_manager.set_task(self.task)
        self.runner = CustomPipelineRunner()

        logger.info("=" * 60)
        logger.info("🚀 STARTING PIPELINE RUNNER")
        logger.info(f"🚀 Initial state: {self.conversation_context.current_state}")
        logger.info(f"🚀 Session: {self.session_id}")
        logger.info(f"🚀 Phone: {self.phone_number}")
        logger.info("=" * 60)

        try:
            await self.runner.run(self.task)
            logger.info("✅ Pipeline completed successfully")

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

        finally:
            # Ensure pipeline cleanup on all exit paths (#16)
            logger.info("Cleaning up pipeline resources...")
            try:
                if self.task:
                    await self.task.cancel()
                if self.transport:
                    # Daily transport cleanup handled by Pipecat
                    pass
                logger.info("Pipeline cleanup complete")
            except Exception as cleanup_error:
                logger.error(f"Error during cleanup: {cleanup_error}")
    
    def get_conversation_state(self) -> Dict[str, Any]:
        """Get current conversation state for monitoring/debugging"""
        if not self.conversation_context:
            return {
                "workflow_state": "inactive",
                "client": self.client_name,
                "patient_data": self.patient_data,
                "phone_number": self.phone_number,
                "transcripts": []
            }
        
        return {
            "workflow_state": "active",
            "client": self.client_name,
            "current_state": self.conversation_context.current_state,
            "patient_data": self.patient_data,
            "phone_number": self.phone_number,
            "transcripts": self.transcripts,
        }