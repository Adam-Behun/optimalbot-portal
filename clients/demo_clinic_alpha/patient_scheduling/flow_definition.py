import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from openai import AsyncOpenAI
from pipecat_flows import (
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)
from loguru import logger

from backend.models import get_async_patient_db
from backend.sessions import get_async_session_db
from backend.utils import parse_natural_date, parse_natural_time
from handlers.transcript import save_transcript_to_db
from clients.demo_clinic_alpha.patient_scheduling.text_conversation import TextConversation


class _MockFlowManager:
    def __init__(self):
        self.state = {}


async def warmup_openai(call_data: dict = None):
    try:
        call_data = call_data or {"organization_name": "Demo Clinic Alpha"}
        flow = PatientSchedulingFlow(
            call_data=call_data,
            session_id="warmup",
            flow_manager=_MockFlowManager(),
            main_llm=None,
        )
        greeting_node = flow.create_greeting_node()

        messages = []
        for msg in greeting_node.get("role_messages") or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        for msg in greeting_node.get("task_messages") or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": "Hello, I'd like to schedule an appointment"})

        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=1,
        )
        logger.info("OpenAI cache warmed with scheduling prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class PatientSchedulingFlow:

    # ==================== Class Constants ====================

    ALLOWS_NEW_PATIENTS = True

    PLACEHOLDER_VALUES = {
        "new", "patient", "unknown", "none", "n/a", "na", "not yet collected",
        "not provided", "not available", "tbd", "pending", "null", "undefined",
    }
    REQUIRED_FIELDS = ["first_name", "last_name", "phone_number", "date_of_birth", "email"]
    PATIENT_INFO_PROPS = {
        "first_name": {"type": "string", "description": "First name if mentioned."},
        "last_name": {"type": "string", "description": "Last name if mentioned."},
        "phone_number": {"type": "string", "description": "Phone if mentioned (digits only)."},
        "email": {"type": "string", "description": "Email if mentioned."},
    }

    # ==================== Initialization ====================

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
        self.organization_name = call_data.get("organization_name", "Demo Clinic Alpha")
        self.cold_transfer_config = cold_transfer_config or {}

        self.today = date.today()
        self.available_slots = self._generate_available_slots()
        self._state_initialized = False

        # Initialize state now if flow_manager exists (cross-workflow handoff)
        # For direct dial-in, runner.py calls _init_flow_state() after setting flow_manager
        if self.flow_manager:
            self._init_state()

    def _init_flow_state(self):
        """Called by runner.py after flow_manager is assigned for direct dial-in."""
        if not self._state_initialized:
            self._init_state()

    def _init_state(self):
        if self._state_initialized:
            return
        self._state_initialized = True

        for field in ["patient_id", "patient_name", "first_name", "last_name", "date_of_birth", "phone_number"]:
            default = None if field == "patient_id" else ""
            self.flow_manager.state[field] = self.flow_manager.state.get(field) or self.call_data.get(field, default)
        self.flow_manager.state["identity_verified"] = self.flow_manager.state.get("identity_verified", False)
        self.flow_manager.state["today"] = self.today.strftime("%B %d, %Y")
        self.flow_manager.state["available_slots"] = self.available_slots

    def _generate_available_slots(self) -> list[str]:
        tomorrow = self.today + timedelta(days=1)
        days_until_friday = (4 - self.today.weekday()) % 7
        if days_until_friday <= 1:
            days_until_friday += 7
        next_friday = self.today + timedelta(days=days_until_friday)
        return [
            f"{tomorrow.strftime('%A, %B %d')} at 9:00 AM",
            f"{next_friday.strftime('%A, %B %d')} at 2:00 PM",
        ]

    # ==================== Helpers: Normalization ====================

    def _normalize_phone(self, phone: str) -> str:
        return ''.join(c for c in phone if c.isdigit())

    def _phone_last4(self, phone: str) -> str:
        return phone[-4:] if len(phone) >= 4 else "***"

    def _end_node(self) -> NodeConfig:
        return NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Thank the patient and say goodbye."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )

    def _is_valid_value(self, value: str) -> bool:
        if not value:
            return False
        return value.lower().strip() not in self.PLACEHOLDER_VALUES

    def _store_volunteered_info(self, args: Dict[str, Any], flow_manager: FlowManager) -> list[str]:
        captured = []
        for field in ["first_name", "last_name", "phone_number", "email"]:
            value = args.get(field, "").strip()
            if self._is_valid_value(value):
                flow_manager.state[field] = value
                captured.append(field)

        dob = args.get("date_of_birth", "").strip()
        if dob:
            parsed_dob = parse_natural_date(dob) or dob
            flow_manager.state["date_of_birth"] = parsed_dob
            captured.append("date_of_birth")

        visit_reason = args.get("visit_reason", "").strip()
        if visit_reason:
            flow_manager.state["appointment_reason"] = visit_reason
            captured.append("visit_reason")

        return captured

    # ==================== Helpers: Database ====================

    async def _try_db_update(self, patient_id: str, method: str, *args, error_msg: str = "DB update error"):
        if not patient_id:
            return
        try:
            db = get_async_patient_db()
            await getattr(db, method)(patient_id, *args, self.organization_id)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")

    # ==================== Helpers: SIP Transfer ====================

    async def _execute_sip_transfer(self, flow_manager: FlowManager) -> NodeConfig:
        staff_number = self.cold_transfer_config.get("staff_number")

        if not staff_number:
            logger.warning("Cold transfer requested but no staff_number configured")
            return self.create_transfer_failed_node()

        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True
            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"SIP transfer initiated: {staff_number}")

            patient_id = flow_manager.state.get("patient_id")
            if patient_id:
                await self._try_db_update(
                    patient_id,
                    "update_call_status", "Transferred",
                    error_msg="Error updating call status"
                )
            return self.create_transfer_initiated_node()
        except Exception as e:
            logger.exception("SIP transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False
            return self.create_transfer_failed_node()

    # ==================== Helpers: Prompts ====================

    def _get_global_instructions(self) -> str:
        return f"""You are Monica, a friendly scheduling assistant for {self.organization_name}.

# What You Handle
You ONLY help patients SCHEDULE NEW APPOINTMENTS. This includes:
- New patients booking their first visit
- Returning patients booking a new appointment

# What You Do NOT Handle - Transfer These
If the caller wants ANY of these, call request_staff to transfer them:
- Check-in for an existing appointment ("I'm here for my appointment", "checking in")
- Cancel or reschedule an existing appointment
- Billing, payments, or account questions
- Insurance or coverage questions
- Medical advice or questions about procedures
- Prescription refills
- Test results or medical records
- Complaints or urgent issues

When transferring, briefly explain: "Let me connect you with someone who can help with that."

# Voice Conversation Style
You are having a real-time phone conversation. Your responses will be converted to speech, so:
- Speak naturally like a human would on the phone—use contractions, brief acknowledgments, and conversational flow
- Keep responses short and direct. One or two sentences is usually enough.
- NEVER use bullet points, numbered lists, asterisks, bold, or any markdown formatting
- Avoid robotic phrases. Say "Got it" or "Perfect" instead of "I have recorded your information"
- Use natural filler when appropriate: "Let me see..." or "Okay, so..."
- If they ask you to repeat, SHORTEN your response each time. Don't repeat verbatim. Example: First time you might say the full slots, second time just "Saturday 9 AM or Friday 2 PM—which works?"

# Handling Speech Recognition
The input you receive is transcribed from speech in real-time and may contain errors. When you notice something that looks wrong:
- Silently correct obvious transcription mistakes based on context
- "buy milk two tomorrow" means "buy milk tomorrow"
- "for too ate" likely means "4 2 8" in a phone number context
- "at gmail dot com" means "@gmail.com"
- If truly unclear, ask them to repeat—but phrase it naturally: "Sorry, I didn't catch that last part"

# Other Guardrails
- If the caller is frustrated or asks for a human: call request_staff to transfer them.
- Never guess at information—always confirm with the patient.

# Data Formats
When collecting emails: "at" → @, "dot" → .
Phone numbers: write as digits only (e.g., "5551234567")."""

    # ==================== Helpers: Function Schemas ====================

    def _get_request_staff_function(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="request_staff",
            description="Transfer to staff. Set urgent=true for emergencies, patient_confirmed=true if they explicitly asked.",
            properties={
                "urgent": {
                    "type": "boolean",
                    "description": "Set true for urgent requests (medical emergencies, pain, swelling). Transfers immediately.",
                },
                "patient_confirmed": {
                    "type": "boolean",
                    "description": "Set true if caller explicitly asked for human/staff transfer. Transfers immediately.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason for transfer (e.g., 'medical_emergency', 'billing', 'reschedule', 'frustrated')",
                },
            },
            required=[],
            handler=self._request_staff_handler,
        )

    # ==================== Node Creators: Entry ====================

    def get_initial_node(self) -> NodeConfig:
        """Entry point for dial-in calls. Returns the first node to execute."""
        return self.create_greeting_node()

    def create_greeting_node(self) -> NodeConfig:
        greeting_text = f"Hello! This is Monica from {self.organization_name}. How can I help you?"

        return NodeConfig(
            name="greeting",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """FIRST: Determine if the caller wants to SCHEDULE a new appointment.

SCHEDULING includes: cleaning, check-up, exam, consultation, follow-up, any type of NEW appointment.
NOT scheduling (transfer these): check-IN for existing appointment ("I'm here for my appointment"), cancel, reschedule an EXISTING appointment, billing, insurance, medical questions.
Note: "follow-up appointment" = scheduling a NEW appointment, not rescheduling.

If they want something OTHER than scheduling:
→ Say "Let me connect you with someone who can help with that." and call request_staff

If they want to SCHEDULE an appointment, ask: "Are you a new patient, or have you been here before?"
Then call the appropriate function:
- CLEARLY NEW: "never been here", "first time", "I'm new" → call set_new_patient immediately
- CLEARLY RETURNING: "I've been here before", "returning patient" → call set_returning_patient immediately
- UNCERTAIN: "I don't remember", "maybe years ago", "not sure" → Say "No problem, can I have your phone number? I'll try to find you in our database." then call set_returning_patient

If they DEFLECT the question (e.g., "does it matter?", "can we just schedule?"):
→ Gently explain why and re-ask: "I just need to know so I can pull up your file or set you up as new. Have you been here before?"
→ Do NOT transfer unless they explicitly ask for a human.

Call the function after they answer. Do NOT ask for name or other info first—the next step handles that.

Capture any info they ALREADY volunteered in the function call, but don't ask for more.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="set_new_patient",
                    description="Call IMMEDIATELY when patient explicitly says they've NEVER been here before. Include any volunteered info.",
                    properties={**self.PATIENT_INFO_PROPS, "visit_reason": {"type": "string", "description": "Reason for visit if mentioned."}},
                    required=[],
                    handler=self._set_new_patient_handler,
                ),
                FlowsFunctionSchema(
                    name="set_returning_patient",
                    description="Call IMMEDIATELY when patient indicates they've EVER been here before. Include any volunteered info.",
                    properties={**self.PATIENT_INFO_PROPS, "visit_reason": {"type": "string", "description": "Reason for visit if mentioned."}},
                    required=[],
                    handler=self._set_returning_patient_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": greeting_text}],
        )

    async def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        context_lower = context.lower()
        is_returning = any(word in context_lower for word in ["returning", "follow-up", "been coming", "three years", "existing"])
        visit_reason = context.split(",")[0] if context else ""

        if visit_reason:
            self.flow_manager.state["appointment_reason"] = visit_reason

        if is_returning:
            self.flow_manager.state["appointment_type"] = "Returning Patient"
            logger.info("Flow: Handoff entry - returning patient, context stored")

            if self.flow_manager.state.get("identity_verified"):
                first_name = self.flow_manager.state.get("first_name", "")
                logger.info(f"Flow: Caller already verified as {first_name}, skipping lookup")
                if self.flow_manager.state.get("appointment_reason"):
                    return self.create_scheduling_node()
                return self.create_visit_reason_node()

            return self.create_returning_patient_lookup_node()
        else:
            self.flow_manager.state["appointment_type"] = "New Patient"
            logger.info("Flow: Handoff entry - new patient, context stored")

            if self.flow_manager.state.get("appointment_reason"):
                return self.create_scheduling_node()
            return self.create_visit_reason_node()

    # ==================== Node Creators: Main Flow ====================

    def create_visit_reason_node(self) -> NodeConfig:
        appointment_type = self.flow_manager.state.get("appointment_type", "")

        return NodeConfig(
            name="visit_reason",
            task_messages=[{
                "role": "system",
                "content": f"""Patient is {appointment_type}. Just ask: "What brings you in today?"
Do NOT add greetings, "thank you", or repeat their name—just ask the question directly.

URGENT (transfer immediately): severe pain, swelling, bleeding, can't eat/sleep, pain for days, emergency
→ Say "That sounds urgent. Let me transfer you to someone who can help right away."
→ Call request_staff with urgent=true

APPOINTMENT TYPES (call save_visit_reason when you identify one):
- Cleaning: "cleaning", "teeth cleaning", "dental cleaning"
- Checkup: "checkup", "check-up", "exam", "examination", "regular checkup", "routine checkup"
- Consultation: "consultation", "whitening", "braces", "invisalign", "cosmetic"
- Follow-up: "follow-up", "post-op", "after [procedure]"
- Treatment: "filling", "crown", "extraction", "root canal"

If they just say "appointment" or don't specify a type, ask: "Sure—is that a cleaning, a checkup, or something else?"
If they mention a provider (e.g., "I'd like to see Dr. Smith"), capture in provider_preference.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_visit_reason",
                    description="Call when patient specifies one of: cleaning, checkup, consultation, follow-up, or treatment. Do NOT call for just 'appointment'.",
                    properties={
                        "reason": {"type": "string", "description": "One of: cleaning, checkup, consultation, follow-up, or specific treatment."},
                        "provider_preference": {"type": "string", "description": "Specific provider if mentioned."},
                    },
                    required=["reason"],
                    handler=self._save_visit_reason_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_scheduling_node(self) -> NodeConfig:
        today = self.flow_manager.state.get("today", "")
        year = self.today.year
        slots = self.flow_manager.state.get("available_slots", [])
        slots_text = " or ".join(slots) if slots else "No slots available"
        email_on_file = self.flow_manager.state.get("email", "")

        return NodeConfig(
            name="scheduling",
            task_messages=[{
                "role": "system",
                "content": f"""TODAY: {today}

Available slots: {slots_text}.
Email on file: {email_on_file or "not yet collected"}

Say the slots conversationally in ONE sentence, like: "I have {slots_text}. Which works for you?"
DO NOT use bullet points, numbered lists, or any formatting.
If they ask you to repeat OR if returning after a brief interruption (like updating email), shorten it: "So, Saturday 9 AM or Friday 2 PM?"

AFTER patient picks a slot, call schedule_appointment with their chosen date/time (use year {year}). Include any volunteered info.
- If they haven't picked a slot yet but volunteer info → call capture_info, then ask which slot they want
- If they want a different day → suggest staff may have more options, offer to transfer
- If they ask about their email on file or want to update it → tell them the email above, and if they give a new one, call capture_info with the new email

Only call request_staff if they EXPLICITLY want to speak with staff.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="schedule_appointment",
                    description="Call after patient confirms date AND time.",
                    properties={
                        "appointment_date": {"type": "string", "description": "Date in 'Month Day, Year' format."},
                        "appointment_time": {"type": "string", "description": "Time in 12-hour format with AM/PM."},
                        **self.PATIENT_INFO_PROPS,
                        "date_of_birth": {"type": "string", "description": "DOB if provided."},
                    },
                    required=["appointment_date", "appointment_time"],
                    handler=self._schedule_appointment_handler,
                ),
                FlowsFunctionSchema(
                    name="capture_info",
                    description="Save volunteered patient info or corrected visit reason.",
                    properties={**self.PATIENT_INFO_PROPS, "date_of_birth": {"type": "string", "description": "DOB if mentioned."}, "reason": {"type": "string", "description": "Visit reason if corrected."}},
                    required=[],
                    handler=self._capture_info_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_collect_info_node(self) -> NodeConfig:
        state = self.flow_manager.state
        fields = {f: state.get(f) for f in self.REQUIRED_FIELDS}
        have = [f"{k}={v}" for k, v in fields.items() if v]
        need = [k for k, v in fields.items() if not v]

        return NodeConfig(
            name="collect_info",
            task_messages=[{
                "role": "system",
                "content": f"""Booking for {state.get("appointment_date", "")} at {state.get("appointment_time", "")}.

ALREADY COLLECTED: {", ".join(have) or "none"}
STILL NEED: {", ".join(need) or "none"}

Only ask for fields in STILL NEED, ONE at a time.
After patient provides the LAST missing field, call save_patient_info with all values.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_patient_info",
                    description="Call after collecting ALL 5 fields with actual values.",
                    properties={f: {"type": "string", "description": f.replace("_", " ").title()} for f in self.REQUIRED_FIELDS},
                    required=self.REQUIRED_FIELDS,
                    handler=self._save_patient_info_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_confirmation_node(self) -> NodeConfig:
        state = self.flow_manager.state
        today = state.get("today", "")

        return NodeConfig(
            name="confirmation",
            task_messages=[{
                "role": "system",
                "content": f"""TODAY: {today}

Confirm BRIEFLY in ONE sentence: "{state.get('first_name', '')}, you're booked for {state.get('appointment_slot', '')}. Confirmation email to {state.get('email', '')}. Anything else?"

DO NOT list or summarize other details. Just the one sentence above.
- If no/goodbye → call end_call
- If correction → call correct_info
- If they want to continue via text/SMS → call continue_via_text
- If they ask about LAB RESULTS → call route_to_workflow with workflow="lab_results"
- If they ask about PRESCRIPTIONS/REFILLS → call route_to_workflow with workflow="prescription_status"
- If question → answer briefly, ask "Anything else?"

If they seem done but you want to offer text: "Would you like me to send you a text? You can reply anytime if questions come up." """,
            }],
            functions=[
                FlowsFunctionSchema(
                    name="route_to_workflow",
                    description="Route to AI workflow. Caller is already verified - context carries through.",
                    properties={
                        "workflow": {"type": "string", "enum": ["lab_results", "prescription_status"], "description": "Workflow type"},
                        "reason": {"type": "string", "description": "Brief context for the next workflow"},
                    },
                    required=["workflow", "reason"],
                    handler=self._route_to_workflow_handler,
                ),
                FlowsFunctionSchema(
                    name="correct_info",
                    description="Patient wants to correct information. appointment_date must be after TODAY.",
                    properties={
                        "field": {"type": "string", "description": "Field to correct"},
                        "new_value": {"type": "string", "description": "The corrected value"},
                    },
                    required=["field", "new_value"],
                    handler=self._correct_info_handler,
                ),
                FlowsFunctionSchema(
                    name="continue_via_text",
                    description="Patient wants to continue conversation over text/SMS.",
                    properties={},
                    required=[],
                    handler=self._offer_text_continuation_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="Patient confirms details and has no more questions.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    # ==================== Node Creators: Bridge ====================

    def _create_bridge_node(self, name: str, target_flow, entry_method: str, transition_message: str = "") -> NodeConfig:
        async def proceed_handler(args, flow_manager):
            return None, getattr(target_flow, entry_method)()

        return NodeConfig(
            name=f"post_{name}",
            task_messages=[{"role": "system", "content": f"Call proceed_to_{name} immediately."}],
            functions=[FlowsFunctionSchema(name=f"proceed_to_{name}", description=f"Proceed to {name}.", properties={}, required=[], handler=proceed_handler)],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": transition_message}] if transition_message else None,
        )

    # ==================== Node Creators: Transfer ====================

    def create_staff_confirmation_node(self) -> NodeConfig:
        return NodeConfig(
            name="staff_confirmation",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """If patient has ALREADY confirmed transfer, call dial_staff IMMEDIATELY.
Otherwise ask once: "Would you like me to transfer you?"
- Positive → dial_staff
- Negative → set_new_patient or set_returning_patient
- If they ask about LAB RESULTS → call route_to_workflow with workflow="lab_results"
- If they ask about PRESCRIPTIONS/REFILLS → call route_to_workflow with workflow="prescription_status" """,
            }],
            functions=[
                FlowsFunctionSchema(name="dial_staff", description="Transfer to staff.", properties={}, required=[], handler=self._dial_staff_handler),
                FlowsFunctionSchema(name="set_new_patient", description="Patient is new, continue scheduling.", properties={"first_name": self.PATIENT_INFO_PROPS["first_name"], "visit_reason": {"type": "string", "description": "Reason if mentioned."}}, required=[], handler=self._set_new_patient_handler),
                FlowsFunctionSchema(name="set_returning_patient", description="Patient has been here, continue scheduling.", properties={"first_name": self.PATIENT_INFO_PROPS["first_name"], "visit_reason": {"type": "string", "description": "Reason if mentioned."}}, required=[], handler=self._set_returning_patient_handler),
                FlowsFunctionSchema(
                    name="route_to_workflow",
                    description="Route to AI workflow for prescriptions or lab results.",
                    properties={
                        "workflow": {"type": "string", "enum": ["lab_results", "prescription_status"], "description": "Workflow type"},
                        "reason": {"type": "string", "description": "Brief context"},
                    },
                    required=["workflow", "reason"],
                    handler=self._route_to_workflow_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_transfer_initiated_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_initiated",
            task_messages=[],
            functions=[],
            pre_actions=[{"type": "tts_say", "text": "Transferring you now, please hold."}],
            post_actions=[{"type": "end_conversation"}],
        )

    def create_transfer_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_failed",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """The transfer didn't go through. Apologize and offer alternatives.

If caller wants to try the transfer again:
→ Call retry_transfer

If caller says goodbye or wants to end call:
→ Call end_call

If caller wants to continue scheduling:
→ Answer their question, then ask if there's anything else""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="retry_transfer",
                    description="Retry the failed transfer.",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the call gracefully.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
            ],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": "I apologize, the transfer didn't go through."}],
        )

    # ==================== Node Creators: Returning Patient Lookup ====================

    def create_returning_patient_lookup_node(self) -> NodeConfig:
        return NodeConfig(
            name="returning_patient_lookup",
            task_messages=[{
                "role": "system",
                "content": """Ask for their phone number to pull up their record: "Let me pull up your file. What's the phone number on your account?"

Once they provide a phone number, call lookup_by_phone with the digits.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="lookup_by_phone",
                    description="Look up patient record by phone number.",
                    properties={
                        "phone_number": {"type": "string", "description": "Phone number (digits only)"},
                    },
                    required=["phone_number"],
                    handler=self._lookup_by_phone_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_returning_patient_verify_dob_node(self) -> NodeConfig:
        return NodeConfig(
            name="returning_patient_verify_dob",
            task_messages=[{
                "role": "system",
                "content": """Found a record. Ask for date of birth to verify: "I found a record. Can you confirm your date of birth?"

Once they provide DOB, call verify_dob.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="verify_dob",
                    description="Verify patient identity by date of birth.",
                    properties={
                        "date_of_birth": {"type": "string", "description": "DOB in natural format"},
                    },
                    required=["date_of_birth"],
                    handler=self._verify_dob_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_returning_patient_not_found_node(self) -> NodeConfig:
        return NodeConfig(
            name="returning_patient_not_found",
            task_messages=[],
            functions=[],
            pre_actions=[{"type": "tts_say", "text": "I couldn't find your record in our system. Let me connect you with a colleague who can help. One moment."}],
            post_actions=[{"type": "end_conversation"}],
        )

    # ==================== Handlers: Patient Type ====================

    async def _set_new_patient_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        flow_manager.state["appointment_type"] = "New Patient"
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: New Patient - captured: {captured if captured else 'none'}")

        if flow_manager.state.get("appointment_reason"):
            return None, self.create_scheduling_node()
        return None, self.create_visit_reason_node()

    async def _set_returning_patient_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        flow_manager.state["appointment_type"] = "Returning Patient"
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: Returning Patient - captured: {captured if captured else 'none'}")

        if flow_manager.state.get("identity_verified"):
            patient_name = flow_manager.state.get("patient_name", "")
            if patient_name:
                if "," in patient_name:
                    parts = [p.strip() for p in patient_name.split(",")]
                    if len(parts) == 2:
                        flow_manager.state["last_name"] = parts[0]
                        flow_manager.state["first_name"] = parts[1]
                else:
                    parts = patient_name.split()
                    if len(parts) >= 2:
                        flow_manager.state["first_name"] = parts[0]
                        flow_manager.state["last_name"] = " ".join(parts[1:])

            first_name = flow_manager.state.get("first_name", "")
            logger.info(f"Flow: Caller already verified as {first_name}, skipping lookup")

            if flow_manager.state.get("appointment_reason"):
                return f"Great, {first_name}! Let me help you schedule that.", self.create_scheduling_node()
            return None, self.create_visit_reason_node()

        return None, self.create_returning_patient_lookup_node()

    async def _save_visit_reason_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        appointment_reason = args.get("reason", "").strip()
        provider_preference = args.get("provider_preference", "").strip()

        flow_manager.state["appointment_reason"] = appointment_reason
        if provider_preference:
            flow_manager.state["provider_preference"] = provider_preference
            logger.info(f"Flow: Visit reason - {appointment_reason}, provider preference - {provider_preference}")
        else:
            logger.info(f"Flow: Visit reason - {appointment_reason}")
        return "Let's get you scheduled.", self.create_scheduling_node()

    # ==================== Handlers: Returning Patient Lookup ====================

    async def _lookup_by_phone_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        phone_digits = self._normalize_phone(args.get("phone_number", ""))
        logger.info(f"Flow: Looking up phone: {self._phone_last4(phone_digits)}")

        if patient := await get_async_patient_db().find_patient_by_phone(phone_digits, self.organization_id):
            flow_manager.state["_lookup_record"] = {f: patient.get(f, "") for f in self.REQUIRED_FIELDS}
            flow_manager.state["_lookup_record"]["patient_id"] = patient.get("patient_id")
            logger.info("Flow: Found record, requesting DOB")
            return None, self.create_returning_patient_verify_dob_node()
        logger.info("Flow: No patient found")
        await self._execute_sip_transfer(flow_manager)
        return None, self.create_returning_patient_not_found_node()

    async def _verify_dob_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        provided = parse_natural_date(args.get("date_of_birth", "").strip())
        lookup = flow_manager.state.get("_lookup_record", {})
        stored = lookup.get("date_of_birth", "")
        logger.info(f"Flow: Verifying DOB - provided: {provided}, stored: {stored}")

        if provided and provided == stored:
            flow_manager.state["identity_verified"] = True
            flow_manager.state.update({f: lookup.get(f, "") for f in self.REQUIRED_FIELDS})
            flow_manager.state["patient_id"] = lookup.get("patient_id")
            flow_manager.state["patient_name"] = f"{lookup.get('first_name', '')} {lookup.get('last_name', '')}".strip()
            del flow_manager.state["_lookup_record"]
            first_name = lookup.get("first_name", "")
            logger.info(f"Flow: DOB verified for {first_name}")
            return f"Welcome back, {first_name}!", self.create_scheduling_node() if flow_manager.state.get("appointment_reason") else self.create_visit_reason_node()

        logger.warning("Flow: DOB mismatch")
        flow_manager.state.pop("_lookup_record", None)
        await self._execute_sip_transfer(flow_manager)
        return "That doesn't match. Let me connect you with a colleague.", self.create_returning_patient_not_found_node()

    # ==================== Handlers: Scheduling ====================

    async def _capture_info_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        captured = self._store_volunteered_info(args, flow_manager)
        if reason := args.get("reason", "").strip():
            flow_manager.state["appointment_reason"] = reason
            captured.append("appointment_reason")
        logger.info(f"Flow: Captured volunteered info: {captured if captured else 'none'}")
        return "Got it.", self.create_scheduling_node()

    async def _schedule_appointment_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        raw_date = args.get("appointment_date", "").strip()
        raw_time = args.get("appointment_time", "").strip()

        self._store_volunteered_info(args, flow_manager)

        appointment_date = parse_natural_date(raw_date) or raw_date
        appointment_time = parse_natural_time(raw_time) or raw_time

        available_slots = flow_manager.state.get("available_slots", [])
        matched_slot = None
        for slot in available_slots:
            slot_lower = slot.lower()
            if appointment_date:
                try:
                    scheduled = date.fromisoformat(appointment_date)
                    slot_date_str = scheduled.strftime("%B %d").lower()
                    time_lower = raw_time.lower()
                    if slot_date_str in slot_lower and time_lower in slot_lower:
                        matched_slot = slot
                        break
                except ValueError:
                    pass

        if not matched_slot:
            slots_text = " or ".join(available_slots)
            logger.warning(f"Flow: Rejected invalid slot: {raw_date} at {raw_time}")
            return f"That slot isn't available. Please choose from: {slots_text}.", self.create_scheduling_node()

        flow_manager.state["appointment_date"] = appointment_date
        flow_manager.state["appointment_time"] = appointment_time
        flow_manager.state["appointment_slot"] = matched_slot
        logger.info(f"Flow: Scheduled {raw_date} → {appointment_date} at {raw_time} → {appointment_time}")

        missing_fields = [f for f in self.REQUIRED_FIELDS if not flow_manager.state.get(f)]

        if not missing_fields:
            logger.info("Flow: All patient info already present, skipping to confirmation")
            return "Perfect! Let me confirm your appointment.", self.create_confirmation_node()

        if flow_manager.state.get("identity_verified") and missing_fields == ["email"]:
            logger.info("Flow: Identity verified, only missing email")
            return "Perfect! I just need your email address to send the confirmation.", self.create_collect_info_node()

        return "Perfect! Now I just need a few details to complete your booking.", self.create_collect_info_node()

    async def _save_patient_info_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        values = {f: args.get(f, "").strip() for f in self.REQUIRED_FIELDS}
        missing = [f for f, v in values.items() if not self._is_valid_value(v)]
        if missing:
            logger.warning(f"Flow: Missing fields: {missing}")
            return f"I still need your {missing[0].replace('_', ' ')}.", self.create_collect_info_node()

        values["date_of_birth"] = parse_natural_date(values["date_of_birth"]) or values["date_of_birth"]
        flow_manager.state.update(values)
        logger.info(f"Flow: Patient info collected - {values['first_name']} {values['last_name']}")

        patient_id = flow_manager.state.get("patient_id")

        if patient_id:
            # Returning patient - update existing
            update = {**values, "patient_name": f"{values['last_name']}, {values['first_name']}"}
            update.update({k: flow_manager.state.get(k) for k in ["appointment_date", "appointment_time", "appointment_type", "appointment_reason"]})
            await self._try_db_update(patient_id, "update_patient", update, error_msg="Error saving patient info")
        else:
            # New patient dial-in - create record
            db = get_async_patient_db()
            patient_data = {
                **values,
                "patient_name": f"{values['last_name']}, {values['first_name']}",
                "organization_id": self.organization_id,
                "workflow": "patient_scheduling",
                "appointment_date": flow_manager.state.get("appointment_date", ""),
                "appointment_time": flow_manager.state.get("appointment_time", ""),
                "appointment_type": flow_manager.state.get("appointment_type", ""),
                "appointment_reason": flow_manager.state.get("appointment_reason", ""),
            }
            patient_id = await db.add_patient(patient_data)
            if patient_id:
                flow_manager.state["patient_id"] = patient_id
                logger.info(f"Flow: Created new patient {patient_id}")

                # Update session with patient_id
                session_db = get_async_session_db()
                await session_db.update_session(self.session_id, {"patient_id": patient_id}, self.organization_id)
            else:
                logger.error("Flow: Failed to create patient record")

        return "Thank you! Let me confirm all the details.", self.create_confirmation_node()

    # ==================== Handlers: Confirmation ====================

    async def _correct_info_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        field = args.get("field", "").strip()
        new_value = args.get("new_value", "").strip()

        if field == "appointment_date":
            parsed = parse_natural_date(new_value)
            if parsed:
                try:
                    corrected_date = date.fromisoformat(parsed)
                    if corrected_date <= self.today:
                        logger.warning(f"Flow: Rejected past date correction: {new_value}")
                        slots = flow_manager.state.get("available_slots", [])
                        slots_text = " or ".join(slots)
                        return f"That date is in the past. Available slots are {slots_text}.", self.create_confirmation_node()
                except ValueError:
                    pass

        if field in flow_manager.state:
            flow_manager.state[field] = new_value
            logger.info(f"Flow: Corrected {field} to {new_value}")
        else:
            logger.warning(f"Flow: Attempted to correct unknown field {field}")

        return f"{new_value}, got it.", self.create_confirmation_node()

    # ==================== Handlers: End Call ====================

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        patient_id = flow_manager.state.get("patient_id")
        session_db = get_async_session_db()
        logger.info("Call ended by flow")

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

        except Exception:
            logger.exception("Error in end_call_handler")
            try:
                await session_db.update_session(self.session_id, {"status": "failed"}, self.organization_id)
            except Exception as e:
                logger.error(f"Failed to update session status: {e}")

        return None, self._end_node()

    # ==================== Handlers: Transfer ====================

    async def _request_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        urgent = args.get("urgent", False)
        patient_confirmed = args.get("patient_confirmed", False)
        reason = args.get("reason", "general inquiry")

        flow_manager.state["transfer_reason"] = reason
        logger.info(f"Flow: Staff transfer requested - reason: {reason}, urgent: {urgent}, confirmed: {patient_confirmed}")

        if urgent or patient_confirmed:
            return None, await self._execute_sip_transfer(flow_manager)

        logger.info("Flow: transitioning to staff_confirmation")
        return None, self.create_staff_confirmation_node()

    async def _dial_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        return None, await self._execute_sip_transfer(flow_manager)

    async def _retry_transfer_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Retrying SIP transfer")
        return None, await self._execute_sip_transfer(flow_manager)

    # ==================== Handlers: Workflow Routing ====================

    async def _route_to_workflow_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")

        flow_manager.state["routed_to"] = f"{workflow} (AI)"
        logger.info(f"Flow: Routing to {workflow} workflow - reason: {reason}")

        if workflow in ("lab_results", "prescription_status"):
            return await self._handoff_to_workflow(workflow, flow_manager, reason)
        logger.warning(f"Unknown workflow: {workflow}")
        return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

    def _create_workflow_flow(self, workflow: str, flow_manager: FlowManager):
        if workflow == "lab_results":
            from clients.demo_clinic_alpha.lab_results.flow_definition import LabResultsFlow
            return LabResultsFlow(
                call_data=self.call_data, session_id=self.session_id, flow_manager=flow_manager,
                main_llm=self.main_llm, context_aggregator=self.context_aggregator,
                transport=self.transport, pipeline=self.pipeline,
                organization_id=self.organization_id, cold_transfer_config=self.cold_transfer_config,
            ), "create_results_node"
        else:
            from clients.demo_clinic_alpha.prescription_status.flow_definition import PrescriptionStatusFlow
            return PrescriptionStatusFlow(
                call_data=self.call_data, session_id=self.session_id, flow_manager=flow_manager,
                main_llm=self.main_llm, context_aggregator=self.context_aggregator,
                transport=self.transport, pipeline=self.pipeline,
                organization_id=self.organization_id, cold_transfer_config=self.cold_transfer_config,
            ), "create_status_node"

    async def _handoff_to_workflow(self, workflow: str, flow_manager: FlowManager, reason: str) -> tuple[None, NodeConfig]:
        target_flow, entry_method = self._create_workflow_flow(workflow, flow_manager)
        logger.info(f"Flow: Handing off to {workflow} - {reason}")

        if flow_manager.state.get("identity_verified"):
            first_name = flow_manager.state.get("first_name", "")
            msg = f"Let me check on that for you, {first_name}." if first_name else "Let me check on that for you."
            return None, self._create_bridge_node(workflow, target_flow, entry_method, msg)
        return None, await target_flow.create_handoff_entry_node(context=reason)

    # ==================== Handlers: Text Conversation ====================

    async def _offer_text_continuation_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        state = flow_manager.state
        if not (phone_number := state.get("phone_number", "")):
            logger.warning("Text continuation requested but no phone number")
            return "I don't have a phone number on file.", self.create_confirmation_node()

        context_fields = ["first_name", "last_name", "phone_number", "email", "date_of_birth", "appointment_date", "appointment_time", "appointment_type", "appointment_reason"]
        patient_id = flow_manager.state.get("patient_id")
        text_conv = TextConversation(
            patient_id=patient_id or "",
            organization_id=self.organization_id,
            organization_name=self.organization_name,
            initial_context={f: state.get(f) for f in context_fields},
        )

        if patient_id:
            await self._try_db_update(patient_id, "update_patient", {"text_conversation_enabled": True, "text_conversation_state": text_conv.to_dict(), "text_handoff_message": text_conv.get_handoff_message()}, error_msg="Error enabling text")
            logger.info(f"Text enabled for {patient_id}")

        return "I'll send you a text right now!", self._end_node()
