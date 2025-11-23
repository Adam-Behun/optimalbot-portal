import logging
from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from pipecat.frames.frames import ManuallySwitchServiceFrame, EndTaskFrame
from pipecat.processors.frame_processor import FrameDirection
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db

logger = logging.getLogger(__name__)


class PatientQuestionsFlow:
    """Dial-in call flow for patient questions workflow - Demo Clinic Beta."""

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager,
                 main_llm, classifier_llm, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None):
        self.patient_data = patient_data
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id

    def _get_global_instructions(self) -> str:
        """Global behavioral rules applied to all states."""
        # Read all fields FLAT from patient_data (no custom_fields nesting)
        patient_name = self.patient_data.get('patient_name', '')
        date_of_birth = self.patient_data.get('date_of_birth', '')
        patient_phone = self.patient_data.get('patient_phone', '')
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        notes = self.patient_data.get('notes', '')

        return f"""PATIENT INFORMATION:
- Patient Name: {patient_name}
- Date of Birth: {date_of_birth}
- Facility: {facility_name}
- Patient Phone: {patient_phone}
- Notes: {notes}

BEHAVIORAL RULES:
1. You are a Virtual Assistant from {facility_name}. Always disclose this.
2. Speak ONLY in English.
3. Maintain a professional, courteous tone.
4. Keep responses concise and natural."""

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting node when caller connects."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="greeting",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""A caller has connected. Greet them warmly.

Say: "Hello, thank you for calling {facility_name}. This is a Virtual Assistant. How can I help you today?"

Listen to their response and call proceed_to_conversation() to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_conversation",
                    description="Transition to conversation node after greeting.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_conversation_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_conversation_node(self) -> NodeConfig:
        """Main conversation node for simple interaction."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="conversation",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": """You're in a simple check-in conversation.

1. Listen to their response
2. Have a brief, natural exchange (1-2 turns)
3. Ask: "Is there anything you'd like me to note for the clinic?"
4. After they respond, thank them and call end_call()

Keep it simple and friendly. This is just a test conversation.

If they want to end the call early, call end_call() immediately."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the conversation after saying goodbye.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    async def _switch_to_classifier_llm(self, action: dict, flow_manager: FlowManager):
        await self.context_aggregator.assistant().push_frame(
            ManuallySwitchServiceFrame(service=self.classifier_llm),
            FrameDirection.UPSTREAM
        )
        logger.info("LLM: classifier (fast greeting)")

    async def _switch_to_main_llm(self, action: dict, flow_manager: FlowManager):
        await self.context_aggregator.assistant().push_frame(
            ManuallySwitchServiceFrame(service=self.main_llm),
            FrameDirection.UPSTREAM
        )
        logger.info("LLM: main (conversation)")

    async def _proceed_to_conversation_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, 'NodeConfig']:
        logger.info("Flow: greeting -> conversation")
        return None, self.create_conversation_node()

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, None]:
        logger.info("Call ended by flow")

        try:
            # Save transcript
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            # Update database status
            patient_id = self.patient_data.get('patient_id')
            if patient_id:
                db = get_async_patient_db()
                await db.update_call_status(patient_id, "Completed", self.organization_id)
                logger.info("Database status: Completed")

            # Push EndTaskFrame for graceful shutdown
            if self.context_aggregator:
                await self.context_aggregator.assistant().push_frame(
                    EndTaskFrame(),
                    FrameDirection.UPSTREAM
                )

        except Exception as e:
            import traceback
            logger.error(f"Error in end_call_handler: {traceback.format_exc()}")

        return None, None
