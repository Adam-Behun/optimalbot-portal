from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from pipecat.frames.frames import ManuallySwitchServiceFrame, EndTaskFrame
from pipecat.processors.frame_processor import FrameDirection
from loguru import logger
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


class PatientQuestionsFlow:
    """Dial-in call flow for patient questions workflow - Demo Clinic Alpha.

    This flow handles incoming patient calls with specific questions to ask.
    All patient fields are read FLAT from patient_data (no custom_fields nesting).
    """

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
        # Read all fields FLAT from patient_data
        patient_name = self.patient_data.get('patient_name', '')
        date_of_birth = self.patient_data.get('date_of_birth', '')
        patient_phone = self.patient_data.get('patient_phone', '')
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Alpha')
        questions = self.patient_data.get('questions', '')
        notes = self.patient_data.get('notes', '')

        return f"""PATIENT INFORMATION:
- Patient Name: {patient_name}
- Date of Birth: {date_of_birth}
- Patient Phone: {patient_phone}
- Facility: {facility_name}
- Questions to Ask: {questions}
- Notes: {notes}

BEHAVIORAL RULES:
1. You are a Virtual Assistant from {facility_name}. Always disclose this.
2. Speak ONLY in English.
3. Maintain a professional, courteous tone.
4. Keep responses concise and natural.
5. Your primary goal is to ask the patient the questions listed above."""

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting node when caller connects."""
        patient_name = self.patient_data.get('patient_name', '')
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Alpha')
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

Listen to their response and call proceed_to_questions() to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_questions",
                    description="Transition to questions node after greeting.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_questions_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_questions_node(self) -> NodeConfig:
        """Main node for asking patient questions."""
        patient_name = self.patient_data.get('patient_name', '')
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Alpha')
        questions = self.patient_data.get('questions', '')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="questions",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You're conducting a patient check-in call.

QUESTIONS TO ASK:
{questions}

WORKFLOW:
1. Ask the questions listed above, one at a time
2. Listen to their responses and acknowledge them
3. If they have concerns, note them and offer to pass the information to the clinic
4. After completing the questions, ask: "Is there anything else you'd like me to note for the clinic?"
5. Thank them and call end_call()

Keep the conversation natural and friendly. If they want to end the call early, call end_call() immediately."""
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
        logger.info("LLM: main (questions)")

    async def _proceed_to_questions_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, 'NodeConfig']:
        logger.info("Flow: greeting -> questions")
        return None, self.create_questions_node()

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
            logger.exception("Error in end_call_handler")

        return None, None
