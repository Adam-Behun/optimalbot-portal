import asyncio
import os
from typing import Dict, Any
from loguru import logger
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat_flows import FlowManager
from pipeline.pipeline_factory import PipelineFactory
from core.flow_loader import discover_warmup_functions
from handlers import (
    setup_transport_handlers,
    setup_transcript_handler,
    setup_safety_handlers,
    setup_output_validator_handlers,
)
from handlers.triage import setup_triage_handlers
from observers import LangfuseLatencyObserver

try:
    from pipecat_whisker import WhiskerObserver
    WHISKER_AVAILABLE = True
except ImportError:
    WHISKER_AVAILABLE = False


class CallSession:
    """Orchestrates a voice call session - builds pipeline, manages flow, handles events."""

    def __init__(
        self,
        client_name: str,
        session_id: str,
        patient_id: str,  # None for dial-in (patient found/created by flow)
        call_data: Dict[str, Any],
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
        self.call_data = call_data
        self.phone_number = phone_number
        self.organization_id = organization_id
        self.organization_slug = organization_slug
        self.call_type = call_type
        self.dialin_settings = dialin_settings
        self.transfer_config = transfer_config
        self.debug_mode = debug_mode

        self.pipeline = None
        self.task = None
        self.flow_manager = None
        self.runner = None
        self.components = None

        # Commonly-accessed component shortcuts
        self.flow = None
        self.transport = None
        self.context_aggregator = None

        self.transcripts = []
        self.transfer_in_progress = False
        self.transcript_saved = False

    def _build_session_data(self) -> dict:
        """Build session data dict for pipeline factory."""
        return {
            'session_id': self.session_id,
            'patient_id': self.patient_id,
            'call_data': self.call_data,
            'phone_number': self.phone_number,
            'organization_id': self.organization_id,
            'organization_slug': self.organization_slug,
            'transcripts': self.transcripts
        }

    async def _warmup_all_flows(self):
        """Warm up OpenAI prompt cache for all flows in the organization."""
        warmup_functions = discover_warmup_functions(self.organization_slug)
        if not warmup_functions:
            logger.debug(f"No warmup functions found for {self.organization_slug}")
            return

        warmup_tasks = [fn(self.call_data) for fn in warmup_functions]
        await asyncio.gather(*warmup_tasks, return_exceptions=True)
        logger.info(f"OpenAI warmed up for {len(warmup_tasks)} flows")

    def _init_from_components(self, components) -> None:
        """Store components and create shortcuts for commonly-accessed fields."""
        self.components = components
        self.flow = components.flow
        self.transport = components.transport
        self.context_aggregator = components.context_aggregator
        self.transcript_processor = components.transcript_processor

    def _create_observers(self) -> list:
        """Create pipeline observers for metrics and debugging."""
        self.latency_observer = LangfuseLatencyObserver(session_id=self.session_id)
        observers = [self.latency_observer]

        whisker_enabled = os.getenv("ENABLE_WHISKER", "false").lower() in ["true", "1", "yes"]
        if self.debug_mode and WHISKER_AVAILABLE and whisker_enabled:
            self.whisker_observer = WhiskerObserver(
                self.pipeline,
                host="localhost",
                port=9090,
                file_name=f"whisker_{self.session_id}.bin"
            )
            observers.append(self.whisker_observer)
            logger.info("ᓚᘏᗢ Whisker debugger enabled - connect to ws://localhost:9090")

        return observers

    def _create_pipeline_task(self, params: PipelineParams, observers: list) -> PipelineTask:
        """Create the pipeline task with tracing and observers."""
        return PipelineTask(
            self.pipeline,
            params=params,
            enable_tracing=True,
            enable_turn_tracking=True,
            conversation_id=self.session_id,
            additional_span_attributes={
                "langfuse.session.id": self.session_id,
                "langfuse.trace.metadata.organization_id": self.organization_id,
                "langfuse.trace.metadata.workflow": self.client_name,
                "langfuse.trace.metadata.phone_number": self.phone_number,
            },
            observers=observers,
        )

    def _init_flow_manager(self) -> None:
        """Initialize FlowManager and wire up flow references."""
        self.flow_manager = FlowManager(
            task=self.task,
            llm=self.components.active_llm,
            tts=self.components.tts,
            context_aggregator=self.components.context_aggregator,
            transport=self.components.transport
        )

        self.flow.flow_manager = self.flow_manager
        self.flow.context_aggregator = self.context_aggregator
        self.flow.transport = self.transport
        self.flow.pipeline = self

        if hasattr(self.flow, '_init_flow_state'):
            self.flow._init_flow_state()

    def _setup_handlers(self) -> None:
        """Register all event handlers."""
        setup_transport_handlers(self, self.call_type)
        setup_transcript_handler(self)

        if self.call_type == "dial-out" and self.components.triage_detector:
            setup_triage_handlers(
                self,
                self.components.triage_detector,
                self.components.ivr_processor,
                self.flow,
                self.flow_manager,
            )

        if self.components.safety_monitor:
            setup_safety_handlers(self, self.components.safety_monitor, self.components.safety_config)

        if self.components.output_validator:
            setup_output_validator_handlers(self, self.components.output_validator, self.components.safety_config)

    async def _execute_pipeline(self) -> None:
        """Run the pipeline and handle completion."""
        self.runner = PipelineRunner()
        try:
            await self.runner.run(self.task)
            logger.info("Call completed successfully")
        except Exception:
            logger.exception("Pipeline error")
            raise

    async def run(self, room_url: str, room_token: str, room_name: str):
        """Main entry point - builds and runs the conversation pipeline."""
        logger.info(f"Starting {self.call_type} call - Client: {self.client_name}, Phone: {self.phone_number}")

        # Build pipeline
        session_data = self._build_session_data()
        room_config = {'room_url': room_url, 'room_token': room_token, 'room_name': room_name}

        self.pipeline, params, components = PipelineFactory.build(
            self.client_name, session_data, room_config, self.dialin_settings
        )

        # Initialize components and start warmup
        self._init_from_components(components)
        warmup_task = asyncio.create_task(self._warmup_all_flows())
        logger.info("Pipeline components assembled")

        # Create task with observers
        observers = self._create_observers()
        self.task = self._create_pipeline_task(params, observers)

        # Initialize FlowManager and handlers
        self._init_flow_manager()
        logger.info("FlowManager initialized")

        self._setup_handlers()
        logger.info("Event handlers registered")

        # Run pipeline
        await self._execute_pipeline()

    def get_conversation_state(self) -> Dict[str, Any]:
        return {
            "workflow_state": "active" if self.flow_manager else "inactive",
            "client": self.client_name,
            "call_data": self.call_data,
            "phone_number": self.phone_number,
            "transcripts": self.transcripts,
        }
