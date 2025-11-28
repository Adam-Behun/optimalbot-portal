import logging
from typing import Any, Dict

from pipecat.frames.frames import EndTaskFrame, ManuallySwitchServiceFrame
from pipecat.processors.frame_processor import FrameDirection

from pipecat_flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)

from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db

logger = logging.getLogger(__name__)


class PatientIntakeFlow:
    """Patient intake flow for Demo Clinic Beta."""

    def __init__(
        self,
        patient_data: Dict[str, Any],
        flow_manager: FlowManager,
        main_llm,
        classifier_llm=None,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
    ):
        self.patient_data = patient_data
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.facility_name = patient_data.get("facility_name", "Demo Clinic Beta")

    # ========== Node Creation Functions ==========

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting node - uses classifier_llm for fast response."""
        return NodeConfig(
            name="greeting",
            role_messages=[
                {
                    "role": "system",
                    "content": f"You are Monica, a friendly Virtual Assistant from {self.facility_name}.",
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": f'Say exactly: "Hello! This is Monica from {self.facility_name}, how can I help you today?" Then immediately call proceed_to_main to continue.',
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_main",
                    description="Proceed to main conversation after greeting.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_main_handler,
                )
            ],
            respond_immediately=True,
            pre_actions=[{"type": "function", "handler": self._switch_to_classifier_llm}],
        )

    def create_patient_type_node(self) -> NodeConfig:
        """Determine if new or returning patient."""
        return NodeConfig(
            name="patient_type",
            role_messages=[
                {
                    "role": "system",
                    "content": f"""You are Monica, a warm and friendly Virtual Assistant from {self.facility_name}.
Be conversational and helpful. Use the patient's name once you learn it.""",
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": """Listen to what the patient needs and determine if they are a NEW patient (first time visiting) or a RETURNING patient (been here before).

If unclear, ask: "Is this your first time visiting us, or have you been here before?"

Once you know:
- If NEW patient: call set_new_patient
- If RETURNING patient: call set_returning_patient""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="set_new_patient",
                    description="Patient is visiting for the first time.",
                    properties={},
                    required=[],
                    handler=self._set_new_patient_handler,
                ),
                FlowsFunctionSchema(
                    name="set_returning_patient",
                    description="Patient has visited before.",
                    properties={},
                    required=[],
                    handler=self._set_returning_patient_handler,
                ),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "function", "handler": self._switch_to_main_llm}],
        )

    def create_visit_reason_node(self) -> NodeConfig:
        """Ask for the reason for the visit after appointment type is determined."""
        appointment_type = self.flow_manager.state.get("appointment_type", "")

        return NodeConfig(
            name="visit_reason",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""The patient is a {appointment_type}.

Ask them about the reason for their visit. Say something like: "What brings you in today?" or "How can we help you today?"

Once they explain, call save_visit_reason with a brief summary of their reason.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="save_visit_reason",
                    description="Save the patient's reason for visiting.",
                    properties={
                        "reason": {
                            "type": "string",
                            "description": "Brief summary of the visit reason (e.g., 'routine checkup', 'tooth pain', 'teeth whitening')",
                        }
                    },
                    required=["reason"],
                    handler=self._save_visit_reason_handler,
                )
            ],
            respond_immediately=True,
        )

    def create_scheduling_node(self) -> NodeConfig:
        """Collect appointment date and time."""
        appointment_type = self.flow_manager.state.get("appointment_type", "appointment")

        available_times = ["9:00 AM", "10:30 AM", "1:00 PM", "3:30 PM"]

        return NodeConfig(
            name="scheduling",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Schedule the patient's appointment.

Ask which day works best for them. When they provide a date, confirm it and offer these available times: {', '.join(available_times)}.

Once they select both a date AND time, call schedule_appointment with the details.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="schedule_appointment",
                    description="Schedule the appointment with the selected date and time.",
                    properties={
                        "appointment_date": {
                            "type": "string",
                            "description": "The selected date (e.g., 'Monday December 15th', 'next Tuesday')",
                        },
                        "appointment_time": {
                            "type": "string",
                            "description": "The selected time slot",
                        },
                    },
                    required=["appointment_date", "appointment_time"],
                    handler=self._schedule_appointment_handler,
                )
            ],
            respond_immediately=False,
        )

    def create_collect_info_node(self) -> NodeConfig:
        """Collect patient information: name, phone, DOB, email."""
        appointment_date = self.flow_manager.state.get("appointment_date", "")
        appointment_time = self.flow_manager.state.get("appointment_time", "")

        return NodeConfig(
            name="collect_info",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Collect patient information to complete the booking for {appointment_date} at {appointment_time}.

Collect the following in a natural conversation:
1. First name
2. Last name
3. Phone number
4. Date of birth
5. Email address

Be conversational - you can collect multiple pieces of info if the patient volunteers them.
Once you have ALL five pieces of information, call save_patient_info.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="save_patient_info",
                    description="Save all collected patient information.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "Patient's last name",
                        },
                        "phone_number": {
                            "type": "string",
                            "description": "Patient's phone number",
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Patient's date of birth",
                        },
                        "email": {
                            "type": "string",
                            "description": "Patient's email address",
                        },
                    },
                    required=["first_name", "last_name", "phone_number", "date_of_birth", "email"],
                    handler=self._save_patient_info_handler,
                )
            ],
            respond_immediately=False,
        )

    def create_confirmation_node(self) -> NodeConfig:
        """Confirm all appointment details with the patient."""
        state = self.flow_manager.state

        return NodeConfig(
            name="confirmation",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Review all details with the patient:

APPOINTMENT DETAILS:
- Appointment Type: {state.get('appointment_type', '')}
- Reason: {state.get('appointment_reason', '')}
- Date: {state.get('appointment_date', '')}
- Time: {state.get('appointment_time', '')}
- Name: {state.get('first_name', '')} {state.get('last_name', '')}
- Phone: {state.get('phone_number', '')}
- DOB: {state.get('date_of_birth', '')}
- Email: {state.get('email', '')}

Read back all the details and ask if everything is correct.
- If they CONFIRM: call confirm_booking
- If they need to CORRECT something: call correct_info with the field and new value""",
                }
            ],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET_WITH_SUMMARY,
                summary_prompt="Summarize: patient type, service requested, appointment date/time. Keep under 30 words.",
            ),
            functions=[
                FlowsFunctionSchema(
                    name="confirm_booking",
                    description="Patient confirms all details are correct - book the appointment.",
                    properties={},
                    required=[],
                    handler=self._confirm_booking_handler,
                ),
                FlowsFunctionSchema(
                    name="correct_info",
                    description="Patient needs to correct some information.",
                    properties={
                        "field": {
                            "type": "string",
                            "description": "Field to correct: first_name, last_name, phone_number, date_of_birth, email, appointment_date, appointment_time",
                        },
                        "new_value": {
                            "type": "string",
                            "description": "The corrected value",
                        },
                    },
                    required=["field", "new_value"],
                    handler=self._correct_info_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_closing_node(self) -> NodeConfig:
        """Final node - thank patient and end call."""
        state = self.flow_manager.state

        return NodeConfig(
            name="closing",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""The appointment is booked! Warmly thank the patient.

Say something like: "Your appointment is all set for {state.get('appointment_date', '')} at {state.get('appointment_time', '')}! You'll receive a confirmation email at {state.get('email', '')}. Thank you for choosing {self.facility_name}, {state.get('first_name', '')}! Have a wonderful day!"

Then call end_call to finish.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the conversation.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                )
            ],
            respond_immediately=True,
        )

    # ========== LLM Switching (only used once) ==========

    async def _switch_to_classifier_llm(self, action: dict, flow_manager: FlowManager):
        """Switch to classifier LLM for fast greeting."""
        if self.context_aggregator and self.classifier_llm:
            await self.context_aggregator.assistant().push_frame(
                ManuallySwitchServiceFrame(service=self.classifier_llm),
                FrameDirection.UPSTREAM,
            )
            logger.info("LLM switched to: classifier_llm")

    async def _switch_to_main_llm(self, action: dict, flow_manager: FlowManager):
        """Switch to main LLM for conversation handling."""
        if self.context_aggregator and self.main_llm:
            await self.context_aggregator.assistant().push_frame(
                ManuallySwitchServiceFrame(service=self.main_llm),
                FrameDirection.UPSTREAM,
            )
            logger.info("LLM switched to: main_llm")

    # ========== Function Handlers ==========

    async def _proceed_to_main_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Transition from greeting to main conversation."""
        logger.info("Flow: greeting → patient_type")
        return "", self.create_patient_type_node()

    async def _set_new_patient_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Patient is new."""
        flow_manager.state["appointment_type"] = "New Patient"
        logger.info("Flow: New Patient")
        return "Welcome!", self.create_visit_reason_node()

    async def _set_returning_patient_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Patient is returning."""
        flow_manager.state["appointment_type"] = "Returning Patient"
        logger.info("Flow: Returning Patient")
        return "Welcome back!", self.create_visit_reason_node()

    async def _save_visit_reason_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save visit reason and proceed to scheduling."""
        flow_manager.state["appointment_reason"] = args.get("reason", "").strip()
        logger.info(f"Flow: Visit reason - {flow_manager.state['appointment_reason']}")
        return "Let's get you scheduled.", self.create_scheduling_node()

    async def _schedule_appointment_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save appointment date and time."""
        flow_manager.state["appointment_date"] = args.get("appointment_date", "").strip()
        flow_manager.state["appointment_time"] = args.get("appointment_time", "").strip()
        logger.info(
            f"Flow: Scheduled {flow_manager.state['appointment_date']} at {flow_manager.state['appointment_time']}"
        )
        return "Perfect! Now I just need a few details to complete your booking.", self.create_collect_info_node()

    async def _save_patient_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save all patient information."""
        flow_manager.state["first_name"] = args.get("first_name", "").strip()
        flow_manager.state["last_name"] = args.get("last_name", "").strip()
        flow_manager.state["phone_number"] = args.get("phone_number", "").strip()
        flow_manager.state["date_of_birth"] = args.get("date_of_birth", "").strip()
        flow_manager.state["email"] = args.get("email", "").strip()
        logger.info(
            f"Flow: Patient info collected - {flow_manager.state['first_name']} {flow_manager.state['last_name']}"
        )
        return "Thank you! Let me confirm all the details.", self.create_confirmation_node()

    async def _correct_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Correct a piece of information."""
        field = args.get("field", "").strip()
        new_value = args.get("new_value", "").strip()

        if field in flow_manager.state:
            flow_manager.state[field] = new_value
            logger.info(f"Flow: Corrected {field} to {new_value}")

        return f"Updated {field}. Let me confirm the details again.", self.create_confirmation_node()

    async def _confirm_booking_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Book the appointment in the database."""
        state = flow_manager.state
        logger.info(f"Flow: Booking appointment for {state.get('first_name')} {state.get('last_name')}")

        try:
            db = get_async_patient_db()
            patient_id = self.patient_data.get("patient_id")

            if patient_id:
                update_fields = {
                    "first_name": state.get("first_name"),
                    "last_name": state.get("last_name"),
                    "patient_name": f"{state.get('last_name')}, {state.get('first_name')}",
                    "phone_number": state.get("phone_number"),
                    "date_of_birth": state.get("date_of_birth"),
                    "email": state.get("email"),
                    "appointment_date": state.get("appointment_date"),
                    "appointment_time": state.get("appointment_time"),
                    "appointment_type": state.get("appointment_type"),
                    "appointment_reason": state.get("appointment_reason"),
                    "call_status": "Completed",
                }
                await db.update_patient(patient_id, update_fields, self.organization_id)
                logger.info(f"Patient record updated: {patient_id}")

            return "Appointment booked successfully!", self.create_closing_node()

        except Exception as e:
            logger.error(f"Error booking appointment: {e}")
            return "I apologize, there was an issue. Let me try again.", self.create_confirmation_node()

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, None]:
        """End the call and save transcript."""
        logger.info("Call ended by flow")
        patient_id = self.patient_data.get("patient_id")
        db = get_async_patient_db() if patient_id else None

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            if patient_id and db:
                await db.update_call_status(patient_id, "Completed", self.organization_id)
                logger.info(f"Database status updated: Completed (patient_id: {patient_id})")

            if self.context_aggregator:
                await self.context_aggregator.assistant().push_frame(
                    EndTaskFrame(),
                    FrameDirection.UPSTREAM,
                )

        except Exception as e:
            import traceback

            logger.error(f"Error in end_call_handler: {traceback.format_exc()}")

            if patient_id and db:
                try:
                    await db.update_call_status(patient_id, "Failed", self.organization_id)
                except Exception as db_error:
                    logger.error(f"Failed to update status to Failed: {db_error}")

        return None, None
