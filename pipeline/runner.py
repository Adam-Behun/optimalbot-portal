import asyncio
from typing import Dict, Any
from loguru import logger
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat_flows import FlowManager
from pipeline.pipeline_factory import PipelineFactory
from handlers import (
    setup_transport_handlers,
    setup_transcript_handler,
)
from handlers.triage import setup_triage_handlers
from observers import LangfuseLatencyObserver

try:
    from pipecat_whisker import WhiskerObserver
    WHISKER_AVAILABLE = True
except ImportError:
    WHISKER_AVAILABLE = False


class ConversationPipeline:

    def __init__(
        self,
        client_name: str,
        session_id: str,
        patient_id: str,
        patient_data: Dict[str, Any],
        phone_number: str,
        organization_id: str,
        organization_slug: str,
        call_type: str,
        dialin_settings: Dict[str, str] = None,
        transfer_config: Dict[str, Any] = None,
        debug_mode: bool = False
    ):
        self.client_name = client_name
        self.session_id = session_id
        self.patient_id = patient_id
        self.patient_data = patient_data
        self.phone_number = phone_number
        self.organization_id = organization_id
        self.organization_slug = organization_slug
        self.call_type = call_type
        self.dialin_settings = dialin_settings
        self.transfer_config = transfer_config
        self.debug_mode = debug_mode

        self.pipeline = None
        self.transport = None
        self.task = None
        self.flow_manager = None
        self.flow = None
        self.triage_detector = None
        self.ivr_processor = None
        self.context_aggregator = None
        self.transcript_processor = None
        self.runner = None

        self.transcripts = []
        self.transfer_in_progress = False
        self.transcript_saved = False  # Track if transcript has been saved to prevent duplicates

    async def run(self, room_url: str, room_token: str, room_name: str):
        logger.info(f"✅ Starting {self.call_type} call session - Client: {self.client_name}, Phone: {self.phone_number}")

        session_data = {
            'session_id': self.session_id,
            'patient_id': self.patient_id,
            'patient_data': self.patient_data,
            'phone_number': self.phone_number,
            'organization_id': self.organization_id,
            'organization_slug': self.organization_slug,
            'transcripts': self.transcripts
        }

        room_config = {
            'room_url': room_url,
            'room_token': room_token,
            'room_name': room_name
        }

        self.pipeline, params, self.transport, components = PipelineFactory.build(
            self.client_name,
            session_data,
            room_config,
            self.dialin_settings
        )

        self.flow = components['flow']

        # Start OpenAI warmup early - while pipeline is being assembled
        # This primes OpenAI's prompt cache BEFORE the first user turn
        organization_name = self.patient_data.get("organization_name", "Demo Clinic Beta")
        from clients.demo_clinic_beta.patient_intake.flow_definition import warmup_openai
        warmup_task = asyncio.create_task(warmup_openai(organization_name))
        self.triage_detector = components.get('triage_detector')
        self.ivr_processor = components.get('ivr_processor')
        self.context = components['context']
        self.context_aggregator = components['context_aggregator']
        self.transcript_processor = components['transcript_processor']

        logger.info("✅ Pipeline components assembled")

        # Create latency observer for tracking user-to-bot response time
        self.latency_observer = LangfuseLatencyObserver(
            session_id=self.session_id,
            patient_id=self.patient_id
        )

        # Create Whisker observer for pipeline debugging (only in debug mode)
        observers = [self.latency_observer]
        if self.debug_mode and WHISKER_AVAILABLE:
            self.whisker_observer = WhiskerObserver(
                self.pipeline,
                host="localhost",
                port=9090,
                file_name=f"whisker_{self.session_id}.bin"  # Save session for later review
            )
            observers.append(self.whisker_observer)
            logger.info("ᓚᘏᗢ Whisker debugger enabled - connect to ws://localhost:9090")

        # Use params from factory, which includes audio sample rates and other settings
        self.task = PipelineTask(
            self.pipeline,
            params=params,
            enable_tracing=True,
            enable_turn_tracking=True,
            conversation_id=self.session_id,
            additional_span_attributes={
                # Langfuse-recognized attributes (filterable/queryable)
                "langfuse.user.id": self.patient_id,
                "langfuse.session.id": self.session_id,
                "langfuse.trace.metadata.organization_id": self.organization_id,
                "langfuse.trace.metadata.workflow": self.client_name,
                "langfuse.trace.metadata.phone_number": self.phone_number,
            },
            observers=observers,
        )

        self.flow_manager = FlowManager(
            task=self.task,
            llm=components['active_llm'],
            context_aggregator=self.context_aggregator,
            transport=self.transport
        )

        self.flow.flow_manager = self.flow_manager
        self.flow.context_aggregator = self.context_aggregator
        self.flow.transport = self.transport
        self.flow.pipeline = self

        # Initialize flow state now that flow_manager is available
        if hasattr(self.flow, '_init_flow_state'):
            self.flow._init_flow_state()

        logger.info("✅ FlowManager initialized")

        setup_transport_handlers(self, self.call_type)
        setup_transcript_handler(self)

        if self.call_type == "dial-out" and self.triage_detector:
            setup_triage_handlers(
                self,
                self.triage_detector,
                self.ivr_processor,
                self.flow,
                self.flow_manager,
            )

        logger.info("✅ Event handlers registered")

        self.runner = PipelineRunner()

        try:
            await self.runner.run(self.task)
            logger.info("✅ Call completed successfully")

        except Exception as e:
            logger.exception("Pipeline error")
            raise

    def get_conversation_state(self) -> Dict[str, Any]:
        return {
            "workflow_state": "active" if self.flow_manager else "inactive",
            "client": self.client_name,
            "patient_data": self.patient_data,
            "phone_number": self.phone_number,
            "transcripts": self.transcripts,
        }
