import logging
from typing import Dict, Any
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat_flows import FlowManager
from pipeline.pipeline_factory import PipelineFactory
from handlers import (
    setup_dialout_handlers,
    setup_transcript_handler,
    setup_ivr_handlers,
)

logger = logging.getLogger(__name__)


class ConversationPipeline:

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

        self.pipeline = None
        self.transport = None
        self.task = None
        self.flow_manager = None
        self.flow = None
        self.ivr_navigator = None
        self.context_aggregator = None
        self.transcript_processor = None
        self.runner = None

        self.transcripts = []
        self.transfer_in_progress = False

    async def run(self, room_url: str, room_token: str, room_name: str):
        logger.info(f"✅ Starting call session - Client: {self.client_name}, Phone: {self.phone_number}")

        session_data = {
            'session_id': self.session_id,
            'patient_id': self.patient_id,
            'patient_data': self.patient_data,
            'phone_number': self.phone_number,
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
            room_config
        )

        self.flow = components['flow']
        self.ivr_navigator = components['ivr_navigator']
        self.context_aggregator = components['context_aggregator']
        self.transcript_processor = components['transcript_processor']

        logger.info("✅ Pipeline components assembled")

        # Use params from factory, which includes audio sample rates and other settings
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

        logger.info("✅ FlowManager initialized")

        setup_dialout_handlers(self)
        setup_transcript_handler(self)
        setup_ivr_handlers(self, self.ivr_navigator)

        logger.info("✅ Event handlers registered")

        self.runner = PipelineRunner()

        try:
            await self.runner.run(self.task)
            logger.info("✅ Call completed successfully")

        except Exception as e:
            logger.error(f"❌ Pipeline error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    def get_conversation_state(self) -> Dict[str, Any]:
        return {
            "workflow_state": "active" if self.flow_manager else "inactive",
            "client": self.client_name,
            "patient_data": self.patient_data,
            "phone_number": self.phone_number,
            "transcripts": self.transcripts,
        }
