import os
from datetime import datetime, timezone
from typing import Any, Dict

from openai import AsyncOpenAI
from pipecat_flows import (
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)
from pipecat_flows.types import ActionConfig
from loguru import logger

from backend.models import get_async_patient_db
from backend.sessions import get_async_session_db
from backend.utils import parse_natural_date, parse_natural_time, normalize_sip_endpoint
from handlers.transcript import save_transcript_to_db


class _MockFlowManager:
    def __init__(self):
        self.state = {}


async def warmup_openai(call_data: dict = None):
    try:
        call_data = call_data or {"organization_name": "Demo Clinic Beta"}
        flow = PatientSchedulingFlow(
            call_data=call_data,
            session_id="warmup",
            flow_manager=_MockFlowManager(),
            main_llm=None,
        )
        greeting_node = flow.create_greeting_node()

        messages = []
        for msg in greeting_node.role_messages or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        for msg in greeting_node.task_messages or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": "Hello, I'd like to schedule an appointment"})

        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=1,
        )
        logger.info("OpenAI cache warmed with beta scheduling prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")

class PatientSchedulingFlow:
    def __init__(
        self,
        call_data: Dict[str, Any],
        session_id: str,
        flow_manager: FlowManager,
        main_llm,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
        cold_transfer_config: Dict[str, Any] = None,
    ):
        self.call_data = call_data
        self.session_id = session_id
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.organization_name = call_data.get("organization_name", "Demo Clinic Beta")
        self.cold_transfer_config = cold_transfer_config or {}

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
- If the caller is frustrated or asks for a human: call the request_staff function to transfer them.
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
                self._get_request_staff_function(),
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
                ),
                self._get_request_staff_function(),
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
                ),
                self._get_request_staff_function(),
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

Acknowledge briefly: "Got it." Ask for each piece of information ONE AT A TIME. Wait for their response before moving to the next item.

CRITICAL: You MUST collect ALL 5 pieces of information before calling save_patient_info. DO NOT call the function until you have non-empty values for: first name, last name, phone number, date of birth, AND email. Empty strings are NOT acceptable.

If unclear or incomplete, ask to repeat. Don't guess.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="save_patient_info",
                    description="ONLY call after collecting ALL 5 fields with actual values. NEVER call with empty strings. You must have: first_name, last_name, phone_number, date_of_birth, and email - all non-empty.",
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
                ),
                self._get_request_staff_function(),
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
                    name="end_call",
                    description="Patient confirms details and has no more questions - end with friendly goodbye.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                self._get_request_staff_function(),
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

    def create_staff_confirmation_node(self) -> NodeConfig:
        """Ask patient to confirm they want to speak with a manager."""
        return NodeConfig(
            name="staff_confirmation",
            role_messages=[
                {
                    "role": "system",
                    "content": self._get_global_instructions(),
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": """You just asked if they'd like to speak with a manager.

- If yes/sure/please/okay → call dial_staff
- If no/nevermind/continue → call return_to_conversation""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="dial_staff",
                    description="Transfer to manager when they confirm.",
                    properties={},
                    required=[],
                    handler=self._dial_staff_handler,
                ),
                FlowsFunctionSchema(
                    name="return_to_conversation",
                    description="Return to conversation if they decline transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_conversation_handler,
                ),
            ],
            respond_immediately=False,
            pre_actions=[
                {"type": "tts_say", "text": "Would you like to speak with my manager?"}
            ],
        )

    def create_transfer_initiated_node(self) -> NodeConfig:
        """Node shown while transfer is in progress."""
        return NodeConfig(
            name="transfer_initiated",
            task_messages=[],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )

    def create_transfer_pending_node(self) -> NodeConfig:
        """Node that plays TTS, then executes SIP transfer after speech completes."""
        return NodeConfig(
            name="transfer_pending",
            task_messages=[],
            functions=[],
            pre_actions=[
                {"type": "tts_say", "text": "Transferring you now, please hold."},
                ActionConfig(type="function", handler=self._regular_sip_transfer),
            ],
        )

    async def _regular_sip_transfer(self, action: dict, flow_manager: FlowManager):
        staff_number = normalize_sip_endpoint(self.cold_transfer_config.get("staff_number"))
        if not staff_number:
            logger.warning("No staff transfer number configured")
            return
        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True
            if self.transport:
                error = await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                if error:
                    logger.error(f"SIP transfer failed: {error}")
                    if self.pipeline:
                        self.pipeline.transfer_in_progress = False
                    return
                logger.info(f"SIP transfer initiated: {staff_number}")
            session_db = get_async_session_db()
            await session_db.update_session(self.session_id, {"call_status": "Transferred"}, self.organization_id)
        except Exception:
            logger.exception("SIP transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False

    def create_transfer_failed_node(self) -> NodeConfig:
        """Node shown when transfer fails."""
        return NodeConfig(
            name="transfer_failed",
            role_messages=[
                {
                    "role": "system",
                    "content": self._get_global_instructions(),
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": "The transfer failed. Apologize and continue helping them.",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="continue_conversation",
                    description="Continue with the conversation after failed transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_conversation_handler,
                )
            ],
            respond_immediately=False,
            pre_actions=[
                {"type": "tts_say", "text": "I apologize, the transfer didn't go through."}
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
        patient_id = flow_manager.state.get("patient_id")
        session_db = get_async_session_db()
        logger.info("Call ended by flow - transitioning to end node")

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)

            # Save session metadata
            session_updates = {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "identity_verified": flow_manager.state.get("identity_verified", False),
                "patient_id": patient_id,
            }
            await session_db.update_session(self.session_id, session_updates, self.organization_id)

            # Save workflow-specific data to patient
            if patient_id:
                patient_db = get_async_patient_db()
                patient_updates = {
                    "call_status": "Completed",
                    "last_call_session_id": self.session_id,
                    "appointment_date": flow_manager.state.get("appointment_date"),
                    "appointment_time": flow_manager.state.get("appointment_time"),
                    "appointment_type": flow_manager.state.get("appointment_type"),
                }
                patient_updates = {k: v for k, v in patient_updates.items() if v is not None}
                await patient_db.update_patient(patient_id, patient_updates, self.organization_id)

        except Exception as e:
            logger.exception("Error in end_call_handler")
            try:
                await session_db.update_session(self.session_id, {"status": "failed"}, self.organization_id)
            except Exception as db_error:
                logger.error(f"Failed to update session status: {db_error}")

        return None, self._create_end_node()

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Transition to staff confirmation node to ask if they want a manager."""
        logger.info("Flow: transitioning to staff_confirmation")
        return None, self.create_staff_confirmation_node()

    async def _dial_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        staff_number = normalize_sip_endpoint(self.cold_transfer_config.get("staff_number"))
        if not staff_number:
            logger.warning("Cold transfer requested but no staff_number configured")
            return None, self.create_transfer_failed_node()
        logger.info(f"Cold transfer initiated to: {staff_number}")
        return None, self.create_transfer_pending_node()

    async def _return_to_conversation_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Return to the previous conversation node if they decline transfer."""
        logger.info("Flow: returning to confirmation node")
        return "No problem, let me continue helping you.", self.create_confirmation_node()

    def _get_request_staff_function(self) -> FlowsFunctionSchema:
        """Return the request_staff function schema for use in multiple nodes."""
        return FlowsFunctionSchema(
            name="request_staff",
            description="Call when the patient asks to speak with a human, staff member, or receptionist. Also use if they seem frustrated or confused.",
            properties={},
            required=[],
            handler=self._request_staff_handler,
        )