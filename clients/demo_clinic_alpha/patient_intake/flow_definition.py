import asyncio
import logging
import os
from typing import Any, Dict

from openai import AsyncOpenAI
from pipecat_flows import (
    ContextStrategyConfig,
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)
from pipecat_flows.types import ContextStrategy

from backend.models import get_async_patient_db
from backend.utils import parse_natural_date, parse_natural_time
from handlers.transcript import save_transcript_to_db

logger = logging.getLogger(__name__)


async def warmup_openai(organization_name: str = "Demo Clinic Alpha"):
    """Warm up OpenAI with system prompt prefix for cache hits.

    OpenAI caches prompt prefixes of 1024+ tokens. We need to send a request
    with the same system prompt structure we use in actual calls to prime the cache.
    """
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Build a prompt that matches the structure used in actual calls
        # This needs to be 1024+ tokens for OpenAI to cache it
        global_instructions = f"""You are Monica, a friendly scheduling assistant for {organization_name}.

# Voice Conversation Style
You are having a real-time phone conversation. Your responses will be converted to speech, so:
- Speak naturally like a human would on the phone—use contractions, brief acknowledgments, and conversational flow
- Keep responses short and direct. One or two sentences is usually enough.
- Never use bullet points, numbered lists, special formatting, or markdown
- Avoid robotic phrases. Say "Got it" or "Perfect" instead of "I have recorded your information"
- Use natural filler when appropriate: "Let me see..." or "Okay, so..."

# Handling Speech Recognition
The input you receive is transcribed from speech in real-time and may contain errors. When you notice something that looks wrong:
- Silently correct obvious transcription mistakes based on context
- "buy milk two tomorrow" means "buy milk tomorrow"
- "for too ate" likely means "4 2 8" in a phone number context
- "at gmail dot com" means "@gmail.com"
- If truly unclear, ask them to repeat—but phrase it naturally: "Sorry, I didn't catch that last part"

# Guardrails
- Scheduling only. Redirect pricing, insurance, or medical questions to office staff.
- If the caller is frustrated or asks for a human: "I understand. Let me connect you with our office staff." then end call.
- Never guess at information—always confirm with the patient.

# Data Formats
When collecting emails: "at" → @, "dot" → .
Phone numbers: write as digits only (e.g., "5551234567")."""

        # Simulate the task messages structure to build up token count
        task_context = """Respond warmly, then ask: "Are you a new patient, or have you been here before?"
- NEW patient → call set_new_patient
- RETURNING patient → call set_returning_patient"""

        # Add padding context to reach 1024 tokens (OpenAI's cache threshold)
        # This simulates what the context looks like after a few turns
        conversation_padding = """
Patient is New Patient. Ask: "What brings you in today?"
Once they explain, call save_visit_reason with brief summary.

Ask which day works. Offer times: 9:00 AM, 10:30 AM, 1:00 PM, 3:30 PM.
Once they pick date AND time, call schedule_appointment.

Collect for booking:
1. Ask first name
2. Last name (ask to spell letter by letter)
3. Phone number
4. Date of birth
5. Email (ask to spell letter by letter)

Acknowledge briefly: "Got it." When ALL 5 collected, call save_patient_info.

Confirm appointment details. Confirmation email will be sent. Anything else?
- If no/goodbye → call end_call
- If question → answer, then ask again"""

        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": global_instructions},
                {"role": "system", "content": task_context},
                {"role": "system", "content": conversation_padding},
                {"role": "user", "content": "Hello, I'd like to schedule an appointment"},
                {"role": "assistant", "content": "Hello! I'd be happy to help you schedule an appointment. Are you a new patient, or have you been here before?"},
                {"role": "user", "content": "I'm a new patient"},
            ],
            max_tokens=1,
        )
        logger.info("OpenAI connection warmed up with prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")

class PatientIntakeFlow:
    def __init__(
        self,
        patient_data: Dict[str, Any],
        flow_manager: FlowManager,
        main_llm,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
        warm_transfer_config: Dict[str, Any] = None,
    ):
        self.patient_data = patient_data
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.organization_name = patient_data.get("organization_name", "Demo Clinic Alpha")
        self.warm_transfer_config = warm_transfer_config or {}

    def _get_global_instructions(self) -> str:
        """Global behavioral rules for patient interactions."""
        return f"""You are Monica, a friendly scheduling assistant for {self.organization_name}.

# Voice Conversation Style
You are having a real-time phone conversation. Your responses will be converted to speech, so:
- Speak naturally like a human would on the phone—use contractions, brief acknowledgments, and conversational flow
- Keep responses short and direct. One or two sentences is usually enough.
- Never use bullet points, numbered lists, special formatting, or markdown
- Avoid robotic phrases. Say "Got it" or "Perfect" instead of "I have recorded your information"
- Use natural filler when appropriate: "Let me see..." or "Okay, so..."

# Handling Speech Recognition
The input you receive is transcribed from speech in real-time and may contain errors. When you notice something that looks wrong:
- Silently correct obvious transcription mistakes based on context
- "buy milk two tomorrow" means "buy milk tomorrow"
- "for too ate" likely means "4 2 8" in a phone number context
- "at gmail dot com" means "@gmail.com"
- If truly unclear, ask them to repeat—but phrase it naturally: "Sorry, I didn't catch that last part"

# Guardrails
- Scheduling only. Redirect pricing, insurance, or medical questions to office staff.
- If the caller is frustrated or asks for a human: "I understand. Let me connect you with our office staff." then end call.
- Never guess at information—always confirm with the patient.

# Data Formats
When collecting emails: "at" → @, "dot" → .
Phone numbers: write as digits only (e.g., "5551234567")."""

    # ========== Node Creation Functions ==========

    def create_greeting_node(self) -> NodeConfig:
        greeting_text = f"Hello! This is Monica from {self.organization_name}. How can I help you?"

        return NodeConfig(
            name="greeting",
            role_messages=[
                {
                    "role": "system",
                    "content": self._get_global_instructions(),
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": """Respond warmly, then ask: "Are you a new patient, or have you been here before?"
- "new", "first time", "never been" → call set_new_patient
- "returning", "been here before", "existing" → call set_returning_patient
- If frustrated or asks for human → call request_staff
- Unclear → ask again before calling any function""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="set_new_patient",
                    description="Call when patient confirms they are NEW (first time visiting).",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if they mentioned it (e.g., 'Hi, this is John' → 'John'), or 'unknown' if not mentioned.",
                        }
                    },
                    required=["first_name"],
                    handler=self._set_new_patient_handler,
                ),
                FlowsFunctionSchema(
                    name="set_returning_patient",
                    description="Call when patient confirms they are RETURNING (been here before).",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if they mentioned it (e.g., 'Hi, this is John' → 'John'), or 'unknown' if not mentioned.",
                        }
                    },
                    required=["first_name"],
                    handler=self._set_returning_patient_handler,
                ),
                FlowsFunctionSchema(
                    name="request_staff",
                    description="Patient is frustrated or explicitly asks to speak with a human/staff member.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler,
                ),
            ],
            respond_immediately=False,
            pre_actions=[
                {"type": "tts_say", "text": greeting_text},
            ],
        )

    def create_visit_reason_node(self) -> NodeConfig:
        appointment_type = self.flow_manager.state.get("appointment_type", "")

        return NodeConfig(
            name="visit_reason",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Patient is {appointment_type}. Ask: "What brings you in today?"
Once they explain, call save_visit_reason with brief summary.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="save_visit_reason",
                    description="Call after patient explains their visit reason. Don't call if they only said 'appointment' without details.",
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

        # Demo: hardcoded slots for next week (Dec 2-6, 2025)
        available_slots = [
            "Monday December 2nd at 9:00 AM",
            "Tuesday December 3rd at 10:30 AM",
            "Wednesday December 4th at 1:00 PM",
            "Thursday December 5th at 3:30 PM",
            "Friday December 6th at 9:00 AM",
        ]

        return NodeConfig(
            name="scheduling",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Offer these available slots: {', '.join(available_slots)}.
Once they pick one, call schedule_appointment with the date and time.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="schedule_appointment",
                    description="Call ONLY after patient confirms BOTH date AND time. Don't call with partial info.",
                    properties={
                        "appointment_date": {
                            "type": "string",
                            "description": "The selected date in 'Month Day, Year' format (e.g., 'December 15, 2025', 'January 3, 2025'). Always include the full year.",
                        },
                        "appointment_time": {
                            "type": "string",
                            "description": "The selected time in 12-hour format with AM/PM (e.g., '9:00 AM', '3:30 PM')",
                        },
                    },
                    required=["appointment_date", "appointment_time"],
                    handler=self._schedule_appointment_handler,
                )
            ],
            respond_immediately=True,
        )

    def create_collect_info_node(self) -> NodeConfig:
        """Collect patient information: name, phone, DOB, email."""
        appointment_date = self.flow_manager.state.get("appointment_date", "")
        appointment_time = self.flow_manager.state.get("appointment_time", "")
        existing_first_name = self.flow_manager.state.get("first_name", "")

        # Build first name instruction based on whether we captured it from greeting
        if existing_first_name:
            first_name_instruction = f'Confirm first name "{existing_first_name}"'
        else:
            first_name_instruction = "Ask first name"

        return NodeConfig(
            name="collect_info",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Collect for booking on {appointment_date} at {appointment_time}:
1. {first_name_instruction}
2. Last name (ask to spell letter by letter)
3. Phone number (digits only)
4. Date of birth
5. Email (ask to spell letter by letter)

Acknowledge briefly: "Got it." When ALL 5 collected, call save_patient_info.

If unclear or incomplete, ask to repeat. Don't guess.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="save_patient_info",
                    description="Call ONLY after ALL 5 fields are collected. Don't call with missing info.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "Patient's last name (spelled out)",
                        },
                        "phone_number": {
                            "type": "string",
                            "description": "Patient's phone number as digits only (e.g., '5551234567')",
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Patient's date of birth in 'Month Day, Year' format (e.g., 'January 15, 1990', 'March 3, 1985'). Always include the full year.",
                        },
                        "email": {
                            "type": "string",
                            "description": "Patient's email in written format (convert 'at' → @, 'dot' → .)",
                        },
                    },
                    required=["first_name", "last_name", "phone_number", "date_of_birth", "email"],
                    handler=self._save_patient_info_handler,
                )
            ],
            respond_immediately=True,
        )

    def create_confirmation_node(self) -> NodeConfig:
        state = self.flow_manager.state

        return NodeConfig(
            name="confirmation",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Confirm: "{state.get('first_name', '')}, your appointment is {state.get('appointment_date', '')} at {state.get('appointment_time', '')}. Confirmation email to {state.get('email', '')}. Anything else?"
- If no/goodbye → call end_call
- If they want to correct something → call correct_info
- If frustrated or asks for human → call request_staff
- If question → answer, then ask again""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="correct_info",
                    description="Patient wants to correct information. Call when they say something is wrong.",
                    properties={
                        "field": {
                            "type": "string",
                            "description": "Field to correct: 'first_name', 'last_name', 'phone_number', 'date_of_birth', 'email', 'appointment_date', or 'appointment_time'",
                        },
                        "new_value": {
                            "type": "string",
                            "description": "The corrected value",
                        },
                    },
                    required=["field", "new_value"],
                    handler=self._correct_info_handler,
                ),
                FlowsFunctionSchema(
                    name="request_staff",
                    description="Patient is frustrated or explicitly asks to speak with a human/staff member.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="Patient confirms details and has no more questions - end with friendly goodbye.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
            ],
            respond_immediately=True,
        )

    def _create_end_node(self) -> NodeConfig:
        return NodeConfig(
            name="end",
            task_messages=[
                {
                    "role": "system",
                    "content": "Thank the patient and say goodbye.",
                }
            ],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )

    # ========== Warm Transfer Nodes ==========

    def create_transferring_to_staff_node(self) -> NodeConfig:
        return NodeConfig(
            name="transferring_to_staff",
            task_messages=[
                {
                    "role": "system",
                    "content": "Say: 'I understand. Let me connect you with our office staff. Please hold for just a moment.' Be warm and reassuring.",
                }
            ],
            functions=[],
            pre_actions=[
                {"type": "function", "handler": self._mute_caller},
            ],
            post_actions=[
                {"type": "function", "handler": self._dial_office_staff},
            ],
        )

    def create_staff_briefing_node(self) -> NodeConfig:
        state = self.flow_manager.state
        first_name = state.get("first_name", "Unknown")
        last_name = state.get("last_name", "")
        reason = state.get("appointment_reason", "Not specified")
        appt_date = state.get("appointment_date", "None")
        appt_time = state.get("appointment_time", "")

        return NodeConfig(
            name="staff_briefing",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""You're now speaking to office staff. The patient cannot hear you.

Briefly explain:
- Patient: {first_name} {last_name}
- Visit reason: {reason}
- Appointment: {appt_date} at {appt_time}
- Why transfer: Patient requested to speak with a person.

Ask if they're ready to be connected. When they confirm, call connect_to_patient.""",
                }
            ],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET_WITH_SUMMARY,
                summary_prompt="Briefly summarize: patient name (if known), visit reason (if mentioned), and that they asked to speak with staff. If little info was collected, just say 'Patient requested to speak with staff early in the call.'",
            ),
            functions=[
                FlowsFunctionSchema(
                    name="connect_to_patient",
                    description="Staff is ready - connect them to the waiting patient.",
                    properties={},
                    required=[],
                    handler=self._connect_staff_to_patient_handler,
                )
            ],
            respond_immediately=True,
        )

    def create_warm_transfer_complete_node(self) -> NodeConfig:
        return NodeConfig(
            name="warm_transfer_complete",
            task_messages=[
                {
                    "role": "system",
                    "content": "Say briefly: 'Connecting you now. Goodbye!'",
                }
            ],
            functions=[],
            post_actions=[
                {"type": "function", "handler": self._connect_and_exit},
                {"type": "end_conversation"},
            ],
        )

    # ========== Function Handlers ==========

    async def _set_new_patient_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Patient is new."""
        appointment_type = "New Patient"
        flow_manager.state["appointment_type"] = appointment_type
        first_name = args.get("first_name", "").strip()
        if first_name.lower() != "unknown":
            flow_manager.state["first_name"] = first_name
            logger.info(f"Flow: {appointment_type} - captured name: {first_name}")
        else:
            logger.info(f"Flow: {appointment_type}")
        return None, self.create_visit_reason_node()

    async def _set_returning_patient_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Patient is returning."""
        appointment_type = "Returning Patient"
        flow_manager.state["appointment_type"] = appointment_type
        first_name = args.get("first_name", "").strip()
        if first_name.lower() != "unknown":
            flow_manager.state["first_name"] = first_name
            logger.info(f"Flow: {appointment_type} - captured name: {first_name}")
        else:
            logger.info(f"Flow: {appointment_type}")
        return None, self.create_visit_reason_node()

    async def _save_visit_reason_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save visit reason and proceed to scheduling."""
        appointment_reason = args.get("reason", "").strip()
        flow_manager.state["appointment_reason"] = appointment_reason
        logger.info(f"Flow: Visit reason - {appointment_reason}")
        return "Let's get you scheduled.", self.create_scheduling_node()

    async def _schedule_appointment_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save appointment date and time."""
        raw_date = args.get("appointment_date", "").strip()
        raw_time = args.get("appointment_time", "").strip()

        # Normalize to ISO format for storage
        appointment_date = parse_natural_date(raw_date) or raw_date
        appointment_time = parse_natural_time(raw_time) or raw_time

        flow_manager.state["appointment_date"] = appointment_date
        flow_manager.state["appointment_time"] = appointment_time
        logger.info(f"Flow: Scheduled {raw_date} → {appointment_date} at {raw_time} → {appointment_time}")
        return "Perfect! Now I just need a few details to complete your booking.", self.create_collect_info_node()

    async def _save_patient_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save all patient information to state and database."""
        first_name = args.get("first_name", "").strip()
        last_name = args.get("last_name", "").strip()
        phone_number = args.get("phone_number", "").strip()
        raw_dob = args.get("date_of_birth", "").strip()
        email = args.get("email", "").strip()

        # Normalize date of birth to ISO format
        date_of_birth = parse_natural_date(raw_dob) or raw_dob

        flow_manager.state["first_name"] = first_name
        flow_manager.state["last_name"] = last_name
        flow_manager.state["phone_number"] = phone_number
        flow_manager.state["date_of_birth"] = date_of_birth
        flow_manager.state["email"] = email

        logger.info(f"Flow: Patient info collected - {first_name} {last_name}, DOB: {raw_dob} → {date_of_birth}")

        # Save to database
        try:
            patient_id = self.patient_data.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                update_fields = {
                    "first_name": first_name,
                    "last_name": last_name,
                    "patient_name": f"{last_name}, {first_name}",
                    "phone_number": phone_number,
                    "date_of_birth": date_of_birth,
                    "email": email,
                    "appointment_date": flow_manager.state.get("appointment_date"),
                    "appointment_time": flow_manager.state.get("appointment_time"),
                    "appointment_type": flow_manager.state.get("appointment_type"),
                    "appointment_reason": flow_manager.state.get("appointment_reason"),
                }
                await db.update_patient(patient_id, update_fields, self.organization_id)
                logger.info(f"Patient record saved to database: {patient_id}")
        except Exception as e:
            logger.error(f"Error saving patient info to database: {e}")

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
        else:
            logger.warning(f"Flow: Attempted to correct unknown field {field}")

        return f"{new_value}, got it. Let me confirm the details again.", self.create_confirmation_node()

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

            return "Appointment booked successfully!", self.create_confirmation_node()

        except Exception as e:
            logger.error(f"Error booking appointment: {e}")
            return "I apologize, there was an issue. Let me try again.", self.create_confirmation_node()

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """End the call - transition to end node which handles termination."""
        logger.info("Call ended by flow - transitioning to end node")
        patient_id = self.patient_data.get("patient_id")
        db = get_async_patient_db() if patient_id else None

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            if patient_id and db:
                await db.update_call_status(patient_id, "Completed", self.organization_id)
                logger.info(f"Database status updated: Completed (patient_id: {patient_id})")

        except Exception as e:
            import traceback

            logger.error(f"Error in end_call_handler: {traceback.format_exc()}")

            if patient_id and db:
                try:
                    await db.update_call_status(patient_id, "Failed", self.organization_id)
                except Exception as db_error:
                    logger.error(f"Failed to update status to Failed: {db_error}")

        return None, self._create_end_node()

    # ========== Warm Transfer Handlers ==========

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        logger.info("Flow: Patient requested staff - initiating warm transfer")
        return None, self.create_transferring_to_staff_node()

    async def _mute_caller(self, action: dict, flow_manager: FlowManager):
        if self.transport:
            # For dial-in: caller is the first participant that joined
            participants = self.transport.participants()
            for p in participants.values():
                if not p["info"]["isLocal"]:
                    participant_id = p["id"]
                    # Store caller ID in state for later use
                    flow_manager.state["caller_participant_id"] = participant_id
                    # Mute caller AND make them not hear the bot (they'll wait in silence)
                    await self.transport.update_remote_participants(
                        remote_participants={
                            participant_id: {
                                "permissions": {
                                    "canSend": [],
                                    "canReceive": {"base": False},  # Can't hear anything during transfer
                                }
                            }
                        }
                    )
                    logger.info(f"Muted and isolated caller: {participant_id}")
                    break

    async def _dial_office_staff(self, action: dict, flow_manager: FlowManager):
        office_number = self.warm_transfer_config.get("staff_number")

        if office_number and self.transport:
            flow_manager.state["warm_transfer_in_progress"] = True
            await self.transport.start_dialout({"phoneNumber": office_number})
            logger.info(f"Dialing office staff: {office_number}")
        else:
            logger.error("No staff_number in warm_transfer config")

    async def _connect_staff_to_patient_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        logger.info("Flow: Staff ready - transitioning to connect")
        return None, self.create_warm_transfer_complete_node()

    async def _connect_and_exit(self, action: dict, flow_manager: FlowManager):
        if self.transport:
            # Get caller ID from state (stored during _mute_caller)
            caller_id = flow_manager.state.get("caller_participant_id")

            # Find staff ID (non-local participant that's not the caller)
            staff_id = None
            participants = self.transport.participants()
            for p in participants.values():
                if p["info"]["isLocal"]:
                    continue
                if p["id"] != caller_id:
                    staff_id = p["id"]
                    break

            if caller_id and staff_id:
                # Connect caller and staff directly - they can hear each other
                await self.transport.update_remote_participants(
                    remote_participants={
                        caller_id: {
                            "permissions": {
                                "canSend": ["microphone"],
                                "canReceive": {"base": True},  # Can hear everyone (including staff)
                            },
                            "inputsEnabled": {"microphone": True},
                        },
                        staff_id: {
                            "permissions": {
                                "canReceive": {"base": True},  # Can hear everyone (including caller)
                            },
                        },
                    }
                )
                logger.info(f"Connected caller ({caller_id}) and staff ({staff_id})")
            else:
                logger.error(f"Could not connect: caller={caller_id}, staff={staff_id}")