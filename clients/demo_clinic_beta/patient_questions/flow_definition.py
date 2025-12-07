# this is the first flow into which a patient's call is routed
# from here the patient can be scheduled for an appointment or routed to a live agent or get any other questions answered

from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from pipecat.frames.frames import EndTaskFrame
from pipecat.processors.frame_processor import FrameDirection
from loguru import logger
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


class PatientQuestionsFlow:
    """Dial-in call flow for patient intake - Demo Clinic Beta.

    Collects patient first name, last name (with spelling confirmation), and date of birth.
    """

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager,
                 main_llm, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None):
        self.patient_data = patient_data  # Will be empty/minimal for inbound calls
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        # classifier_llm not used - single LLM flow
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id

        # Collected patient info during the call
        self.collected_first_name = None
        self.collected_last_name = None
        self.collected_dob = None

    def _get_global_instructions(self) -> str:
        """Global behavioral rules applied to all states."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        return f"""BEHAVIORAL RULES:
1. You are a Virtual Assistant from {facility_name}. Always disclose this.
2. Speak ONLY in English.
3. Maintain a professional, courteous tone.
4. Keep responses concise and natural.
5. This is an inbound call - the patient is calling us. We do not know who they are yet."""

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

Say something like: "Hello, thank you for calling {facility_name}. This is a Virtual Assistant. To help you today, I'll need to collect some information. May I have your first name please?"

After the caller provides their first name, call save_first_name with the name they provided."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_first_name",
                    description="Save the patient's first name and proceed to collect last name.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "The patient's first name"
                        }
                    },
                    required=["first_name"],
                    handler=self._save_first_name_handler
                )
            ],
            respond_immediately=True
        )

    def create_collect_last_name_node(self) -> NodeConfig:
        """Node to collect patient's last name."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="collect_last_name",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}

COLLECTED INFO SO FAR:
- First Name: {self.collected_first_name}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You've collected the patient's first name: {self.collected_first_name}.

Now ask for their last name. Say something like: "Thank you, {self.collected_first_name}. And what is your last name?"

After they provide their last name, call save_last_name with the name they provided."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_last_name",
                    description="Save the patient's last name and proceed to spelling confirmation.",
                    properties={
                        "last_name": {
                            "type": "string",
                            "description": "The patient's last name"
                        }
                    },
                    required=["last_name"],
                    handler=self._save_last_name_handler
                )
            ],
            respond_immediately=True
        )

    def create_confirm_spelling_node(self) -> NodeConfig:
        """Node to confirm the spelling of patient's last name."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="confirm_spelling",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}

COLLECTED INFO SO FAR:
- First Name: {self.collected_first_name}
- Last Name: {self.collected_last_name}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You've collected the patient's last name: {self.collected_last_name}.

Now confirm the spelling. Spell out the last name letter by letter and ask if it's correct.
Say something like: "Let me confirm the spelling of your last name. Is it spelled {' '.join(self.collected_last_name.upper())}?"

- If they confirm it's correct, call confirm_spelling_correct.
- If they say it's wrong or provide a correction, call update_last_name_spelling with the corrected spelling."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="confirm_spelling_correct",
                    description="Confirm the last name spelling is correct and proceed to collect date of birth.",
                    properties={},
                    required=[],
                    handler=self._confirm_spelling_correct_handler
                ),
                FlowsFunctionSchema(
                    name="update_last_name_spelling",
                    description="Update the last name with correct spelling and re-confirm.",
                    properties={
                        "corrected_last_name": {
                            "type": "string",
                            "description": "The corrected spelling of the patient's last name"
                        }
                    },
                    required=["corrected_last_name"],
                    handler=self._update_last_name_spelling_handler
                )
            ],
            respond_immediately=True
        )

    def create_collect_dob_node(self) -> NodeConfig:
        """Node to collect patient's date of birth."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="collect_dob",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}

COLLECTED INFO SO FAR:
- First Name: {self.collected_first_name}
- Last Name: {self.collected_last_name}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You've confirmed the patient's name: {self.collected_first_name} {self.collected_last_name}.

Now ask for their date of birth. Say something like: "Thank you. And what is your date of birth?"

After they provide their date of birth, call save_date_of_birth with the date.
Accept various formats (e.g., "January 15, 1985", "1/15/85", "01-15-1985") and normalize to a consistent format."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_date_of_birth",
                    description="Save the patient's date of birth and proceed to confirmation.",
                    properties={
                        "date_of_birth": {
                            "type": "string",
                            "description": "The patient's date of birth (e.g., 'January 15, 1985' or '01/15/1985')"
                        }
                    },
                    required=["date_of_birth"],
                    handler=self._save_dob_handler
                )
            ],
            respond_immediately=True
        )

    def create_confirmation_node(self) -> NodeConfig:
        """Node to confirm all collected information and save to database."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="confirmation",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}

COLLECTED PATIENT INFO:
- First Name: {self.collected_first_name}
- Last Name: {self.collected_last_name}
- Date of Birth: {self.collected_dob}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You've collected all the patient information:
- Name: {self.collected_first_name} {self.collected_last_name}
- Date of Birth: {self.collected_dob}

Confirm this information with the patient. Say something like:
"Let me confirm your information. Your name is {self.collected_first_name} {self.collected_last_name}, and your date of birth is {self.collected_dob}. Is that correct?"

- If they confirm, call save_patient_to_database to save their information.
- If they need to correct something, ask what needs to be corrected and call the appropriate correction function."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_patient_to_database",
                    description="Save all patient information to the database and complete the intake.",
                    properties={},
                    required=[],
                    handler=self._save_patient_to_database_handler
                ),
                FlowsFunctionSchema(
                    name="correct_first_name",
                    description="Go back to correct the first name.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "The corrected first name"
                        }
                    },
                    required=["first_name"],
                    handler=self._correct_first_name_handler
                ),
                FlowsFunctionSchema(
                    name="correct_last_name",
                    description="Go back to correct the last name.",
                    properties={
                        "last_name": {
                            "type": "string",
                            "description": "The corrected last name"
                        }
                    },
                    required=["last_name"],
                    handler=self._correct_last_name_handler
                ),
                FlowsFunctionSchema(
                    name="correct_date_of_birth",
                    description="Go back to correct the date of birth.",
                    properties={
                        "date_of_birth": {
                            "type": "string",
                            "description": "The corrected date of birth"
                        }
                    },
                    required=["date_of_birth"],
                    handler=self._correct_dob_handler
                )
            ],
            respond_immediately=True
        )

    def create_closing_node(self) -> NodeConfig:
        """Final node after successful intake."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="closing",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}.

{global_instructions}

PATIENT INFO (SAVED):
- Name: {self.collected_first_name} {self.collected_last_name}
- Date of Birth: {self.collected_dob}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""The patient's information has been saved successfully.

Thank them and ask if there's anything else you can help with. Say something like:
"Thank you, {self.collected_first_name}. Your information has been saved. Is there anything else I can help you with today?"

- If they need more help, continue the conversation.
- When they're ready to end the call, call end_call."""
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
            respond_immediately=True
        )

    # Handler functions

    async def _save_first_name_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save first name and transition to last name collection."""
        self.collected_first_name = args.get("first_name", "").strip()
        logger.info(f"Flow: Saved first name: {self.collected_first_name}")
        return f"First name '{self.collected_first_name}' saved.", self.create_collect_last_name_node()

    async def _save_last_name_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save last name and transition to spelling confirmation."""
        self.collected_last_name = args.get("last_name", "").strip()
        logger.info(f"Flow: Saved last name: {self.collected_last_name}")
        return f"Last name '{self.collected_last_name}' saved.", self.create_confirm_spelling_node()

    async def _confirm_spelling_correct_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Confirm spelling is correct and move to DOB collection."""
        logger.info(f"Flow: Last name spelling confirmed: {self.collected_last_name}")
        return "Spelling confirmed.", self.create_collect_dob_node()

    async def _update_last_name_spelling_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Update last name spelling and re-confirm."""
        self.collected_last_name = args.get("corrected_last_name", "").strip()
        logger.info(f"Flow: Updated last name spelling: {self.collected_last_name}")
        return f"Last name updated to '{self.collected_last_name}'.", self.create_confirm_spelling_node()

    async def _save_dob_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save date of birth and transition to final confirmation."""
        self.collected_dob = args.get("date_of_birth", "").strip()
        logger.info(f"Flow: Saved DOB: {self.collected_dob}")
        return f"Date of birth '{self.collected_dob}' saved.", self.create_confirmation_node()

    async def _correct_first_name_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Correct first name and return to confirmation."""
        self.collected_first_name = args.get("first_name", "").strip()
        logger.info(f"Flow: Corrected first name: {self.collected_first_name}")
        return f"First name updated to '{self.collected_first_name}'.", self.create_confirmation_node()

    async def _correct_last_name_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Correct last name and go to spelling confirmation."""
        self.collected_last_name = args.get("last_name", "").strip()
        logger.info(f"Flow: Corrected last name: {self.collected_last_name}")
        return f"Last name updated to '{self.collected_last_name}'.", self.create_confirm_spelling_node()

    async def _correct_dob_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Correct date of birth and return to confirmation."""
        self.collected_dob = args.get("date_of_birth", "").strip()
        logger.info(f"Flow: Corrected DOB: {self.collected_dob}")
        return f"Date of birth updated to '{self.collected_dob}'.", self.create_confirmation_node()

    async def _save_patient_to_database_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Update existing patient record with collected info."""
        logger.info(f"Flow: Updating patient in database - {self.collected_first_name} {self.collected_last_name}, DOB: {self.collected_dob}")

        try:
            db = get_async_patient_db()
            patient_id = self.patient_data.get('patient_id')

            if not patient_id:
                logger.error("No patient_id found in patient_data - cannot update")
                return "I apologize, there was a technical issue. Let me try again.", self.create_confirmation_node()

            # Update fields on the existing patient record
            update_fields = {
                "first_name": self.collected_first_name,
                "last_name": self.collected_last_name,
                "patient_name": f"{self.collected_last_name}, {self.collected_first_name}",
                "date_of_birth": self.collected_dob
            }

            success = await db.update_patient(patient_id, update_fields, self.organization_id)

            if success:
                logger.info(f"Patient updated in database: {patient_id}")
                return "Patient information saved successfully.", self.create_closing_node()
            else:
                logger.error(f"Failed to update patient {patient_id}")
                return "I apologize, there was an issue saving your information. Let me try again.", self.create_confirmation_node()

        except Exception as e:
            logger.error(f"Error updating patient in database: {e}")
            return "I apologize, there was a technical issue. Let me try again.", self.create_confirmation_node()

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, None]:
        """End the call and save transcript."""
        logger.info("Call ended by flow")
        patient_id = self.patient_data.get('patient_id')
        db = get_async_patient_db() if patient_id else None

        try:
            # Save transcript
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            # Update database status to Completed
            if patient_id and db:
                await db.update_call_status(patient_id, "Completed", self.organization_id)
                logger.info(f"Database status updated: Completed (patient_id: {patient_id})")

            # Push EndTaskFrame for graceful shutdown
            if self.context_aggregator:
                await self.context_aggregator.assistant().push_frame(
                    EndTaskFrame(),
                    FrameDirection.UPSTREAM
                )

        except Exception as e:
            logger.exception("Error in end_call_handler")

            # Update status to Failed on error
            if patient_id and db:
                try:
                    await db.update_call_status(patient_id, "Failed", self.organization_id)
                    logger.info(f"Database status updated: Failed (patient_id: {patient_id})")
                except Exception as db_error:
                    logger.error(f"Failed to update status to Failed: {db_error}")

        return None, None
