import os
from datetime import date, timedelta
from typing import Any, Dict

from openai import AsyncOpenAI
from pipecat_flows import (
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)
from loguru import logger

from backend.models import get_async_patient_db
from backend.utils import parse_natural_date, parse_natural_time
from handlers.transcript import save_transcript_to_db
from clients.demo_clinic_alpha.patient_scheduling.text_conversation import TextConversation


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

        # Simulate the task messages structure to build up token count
        task_context = """FIRST: Determine if the caller wants to SCHEDULE a new appointment.

If they want something OTHER than scheduling (check-in, cancel, reschedule, billing, insurance, medical question, etc.):
→ Say "Let me connect you with someone who can help with that." and call request_staff

If they want to SCHEDULE an appointment, ask: "Are you a new patient, or have you been here before?"
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

class PatientSchedulingFlow:
    def __init__(
        self,
        patient_data: Dict[str, Any],
        flow_manager: FlowManager,
        main_llm,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
        cold_transfer_config: Dict[str, Any] = None,
    ):
        self.patient_data = patient_data
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.organization_name = patient_data.get("organization_name", "Demo Clinic Alpha")
        self.cold_transfer_config = cold_transfer_config or {}

        # Set current date and available slots in state for reference across flow
        self.today = date.today()
        self.flow_manager.state["today"] = self.today.strftime("%B %d, %Y")

        # Generate available slots (will come from EHR integration later)
        self.available_slots = self._generate_available_slots()
        self.flow_manager.state["available_slots"] = self.available_slots

    def _generate_available_slots(self) -> list[str]:
        """Generate available appointment slots. Will be replaced by EHR integration."""
        tomorrow = self.today + timedelta(days=1)

        # Find next Friday that's at least 2 days away (0=Monday, 4=Friday)
        days_until_friday = (4 - self.today.weekday()) % 7
        if days_until_friday <= 1:  # If Friday is tomorrow or today, get next week's Friday
            days_until_friday += 7
        next_friday = self.today + timedelta(days=days_until_friday)

        return [
            f"{tomorrow.strftime('%A, %B %d')} at 9:00 AM",
            f"{next_friday.strftime('%A, %B %d')} at 2:00 PM",
        ]

    def _get_global_instructions(self) -> str:
        """Global behavioral rules for patient interactions."""
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
                    "content": """FIRST: Determine if the caller wants to SCHEDULE a new appointment.

SCHEDULING includes: cleaning, check-up, exam, consultation, follow-up, any type of NEW appointment.
NOT scheduling (transfer these): check-IN for existing appointment ("I'm here for my appointment"), cancel, reschedule an EXISTING appointment, billing, insurance, medical questions.
Note: "follow-up appointment" = scheduling a NEW appointment, not rescheduling.

If they want something OTHER than scheduling:
→ Say "Let me connect you with someone who can help with that." and call request_staff

If they want to SCHEDULE an appointment, ask: "Are you a new patient, or have you been here before?"
Then IMMEDIATELY call the appropriate function—don't ask for more info first:
- "never been here" / "first time" / "I'm new" → call set_new_patient
- EVER been here (even years ago, even once, even uncertain) → call set_returning_patient (we'll look them up by phone in the next step)

If they DEFLECT the question (e.g., "does it matter?", "can we just schedule?"):
→ Gently explain why and re-ask: "I just need to know so I can pull up your file or set you up as new. Have you been here before?"
→ Do NOT transfer unless they explicitly ask for a human.

Call the function IMMEDIATELY after they answer. Do NOT ask for name, phone, or other info first—the next step handles that.

Capture any info they ALREADY volunteered in the function call, but don't ask for more.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="set_new_patient",
                    description="Call IMMEDIATELY when patient explicitly says they've NEVER been here before (e.g., 'I'm new', 'first time'). Include any volunteered info.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if mentioned. Omit or leave empty if not mentioned.",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "Patient's last name if mentioned, or empty string.",
                        },
                        "phone_number": {
                            "type": "string",
                            "description": "Phone number if mentioned (digits only), or empty string.",
                        },
                        "email": {
                            "type": "string",
                            "description": "Email if mentioned, or empty string.",
                        },
                        "visit_reason": {
                            "type": "string",
                            "description": "Reason for visit if mentioned (e.g., 'cleaning', 'tooth pain'), or empty string.",
                        },
                    },
                    required=[],
                    handler=self._set_new_patient_handler,
                ),
                FlowsFunctionSchema(
                    name="set_returning_patient",
                    description="Call IMMEDIATELY when patient indicates they've EVER been here before (even years ago, even once, even if uncertain). We'll verify in database. Include any volunteered info.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if mentioned. Omit or leave empty if not mentioned.",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "Patient's last name if mentioned, or empty string.",
                        },
                        "phone_number": {
                            "type": "string",
                            "description": "Phone number if mentioned (digits only), or empty string.",
                        },
                        "email": {
                            "type": "string",
                            "description": "Email if mentioned, or empty string.",
                        },
                        "visit_reason": {
                            "type": "string",
                            "description": "Reason for visit if mentioned (e.g., 'cleaning', 'tooth pain'), or empty string.",
                        },
                    },
                    required=[],
                    handler=self._set_returning_patient_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=False,
            pre_actions=[
                {"type": "tts_say", "text": greeting_text},
            ],
        )

    def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        """Entry point when handed off from mainline flow. No greeting, uses gathered context."""
        # Parse context to extract key info
        context_lower = context.lower()
        is_returning = any(word in context_lower for word in ["returning", "follow-up", "been coming", "three years", "existing"])

        # Extract visit reason and doctor preference from context
        visit_reason = context.split(",")[0] if context else ""  # First part is usually the reason

        # Pre-populate state with extracted context
        if visit_reason:
            self.flow_manager.state["appointment_reason"] = visit_reason

        # Pre-set state based on context (the mainline already acknowledged)
        if is_returning:
            self.flow_manager.state["appointment_type"] = "Returning Patient"

        return NodeConfig(
            name="handoff_entry",
            role_messages=[
                {
                    "role": "system",
                    "content": self._get_global_instructions(),
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": f"""CONTEXT: {context}

CRITICAL: Do NOT generate any text. Only call the function. No acknowledgment, no greeting, no words at all.

Call the appropriate function immediately based on context:
- "returning" / "follow-up" / "been here" → set_returning_patient
- "new" / "first time" → set_new_patient

Include visit_reason and doctor_preference in the function arguments.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="set_new_patient",
                    description="Patient is new. Include volunteered info.",
                    properties={
                        "visit_reason": {"type": "string", "description": "Why they're coming in"},
                    },
                    required=[],
                    handler=self._set_new_patient_handler,
                ),
                FlowsFunctionSchema(
                    name="set_returning_patient",
                    description="Patient has been here before. Include all context.",
                    properties={
                        "visit_reason": {"type": "string", "description": "Why they're coming in"},
                        "doctor_preference": {"type": "string", "description": "Preferred doctor if mentioned"},
                    },
                    required=[],
                    handler=self._set_returning_patient_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_visit_reason_node(self) -> NodeConfig:
        appointment_type = self.flow_manager.state.get("appointment_type", "")

        return NodeConfig(
            name="visit_reason",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Patient is {appointment_type}. Just ask: "What brings you in today?"
Do NOT add greetings, "thank you", or repeat their name—just ask the question directly.

If they describe URGENT symptoms (severe pain, swelling, bleeding, can't eat/sleep, pain for days, emergency):
→ Say "That sounds urgent. Let me transfer you to someone who can help right away."
→ Call request_staff with urgent=true (this transfers immediately)

For routine visits (cleaning, checkup, consultation, follow-up):
→ Call save_visit_reason with brief summary""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="save_visit_reason",
                    description="Call for ROUTINE visits only (cleaning, checkup, consultation). Do NOT use for urgent/painful issues.",
                    properties={
                        "reason": {
                            "type": "string",
                            "description": "Brief summary: 'routine checkup', 'cleaning', 'consultation', etc.",
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
        today = self.flow_manager.state.get("today", "")
        year = self.today.year
        slots = self.flow_manager.state.get("available_slots", [])
        slots_text = " or ".join(slots) if slots else "No slots available"
        email_on_file = self.flow_manager.state.get("email", "")

        return NodeConfig(
            name="scheduling",
            task_messages=[
                {
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
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="schedule_appointment",
                    description="Call after patient confirms date AND time. Include any info they volunteered.",
                    properties={
                        "appointment_date": {
                            "type": "string",
                            "description": "The selected date in 'Month Day, Year' format (e.g., 'December 15, 2025'). Always include the full year.",
                        },
                        "appointment_time": {
                            "type": "string",
                            "description": "The selected time in 12-hour format with AM/PM (e.g., '9:00 AM', '3:30 PM')",
                        },
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if volunteered.",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "Patient's last name if volunteered.",
                        },
                        "phone_number": {
                            "type": "string",
                            "description": "Phone number if volunteered (digits only).",
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Date of birth if volunteered (Month Day, Year format).",
                        },
                        "email": {
                            "type": "string",
                            "description": "Email if volunteered.",
                        },
                    },
                    required=["appointment_date", "appointment_time"],
                    handler=self._schedule_appointment_handler,
                ),
                FlowsFunctionSchema(
                    name="capture_info",
                    description="Save volunteered patient info when they provide it before picking a slot.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if mentioned.",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "Patient's last name if mentioned.",
                        },
                        "phone_number": {
                            "type": "string",
                            "description": "Phone number if mentioned (digits only).",
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Date of birth if mentioned (Month Day, Year format).",
                        },
                        "email": {
                            "type": "string",
                            "description": "Email if mentioned.",
                        },
                    },
                    required=[],
                    handler=self._capture_info_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_collect_info_node(self) -> NodeConfig:
        """Collect patient information: name, phone, DOB, email."""
        state = self.flow_manager.state
        appointment_date = state.get("appointment_date", "")
        appointment_time = state.get("appointment_time", "")

        # Build status for each field
        fields = {
            "first_name": state.get("first_name"),
            "last_name": state.get("last_name"),
            "phone_number": state.get("phone_number"),
            "date_of_birth": state.get("date_of_birth"),
            "email": state.get("email"),
        }

        have = [f"{k}={v}" for k, v in fields.items() if v]
        need = [k for k, v in fields.items() if not v]

        have_str = ", ".join(have) if have else "none"
        need_str = ", ".join(need) if need else "none"

        return NodeConfig(
            name="collect_info",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""Booking for {appointment_date} at {appointment_time}.

ALREADY COLLECTED (use these values): {have_str}
STILL NEED: {need_str}

CRITICAL: Only ask for fields in STILL NEED. NEVER re-ask for fields in ALREADY COLLECTED.
Ask for missing info conversationally, ONE field at a time. DO NOT list multiple fields with numbers or bullets.
Example: "Can I get your phone number?" then after they answer, "And your date of birth?"

After patient provides the LAST missing field, IMMEDIATELY call save_patient_info with:
- Values from ALREADY COLLECTED above (copy exactly)
- New value(s) just collected

Format tips:
- Last name: ASK to spell, but accept if they give it clearly. Don't insist on spelling if they refuse.
- Phone: digits only
- Email: ASK to spell, but accept clear answers like "john.doe@email.com"

If patient gives info clearly and refuses to spell, accept it and move on.""",
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
        today = state.get("today", "")

        return NodeConfig(
            name="confirmation",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""TODAY: {today}

Confirm BRIEFLY in ONE sentence: "{state.get('first_name', '')}, you're booked for {state.get('appointment_date', '')} at {state.get('appointment_time', '')}. Confirmation email to {state.get('email', '')}. Anything else?"

DO NOT list or summarize other details. Just the one sentence above.
- If no/goodbye → call end_call
- If correction → call correct_info
- If they want to continue via text/SMS → call continue_via_text
- If they ask about LAB RESULTS → call route_to_workflow with workflow="lab_results"
- If they ask about PRESCRIPTIONS/REFILLS → call route_to_workflow with workflow="prescription_status"
- If question → answer briefly, ask "Anything else?"

If they seem done but you want to offer text: "Would you like me to send you a text? You can reply anytime if questions come up." """,
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="route_to_workflow",
                    description="""Route caller to an AI-powered workflow.

WHEN TO USE: Caller asks about lab results or prescriptions.
RESULT: Hands off to specialized AI workflow (no phone transfer).

IMPORTANT: The caller is already verified - context carries through.

EXAMPLES:
- workflow="lab_results", reason="checking on blood work after scheduling"
- workflow="prescription_status", reason="refill inquiry after scheduling" """,
                    properties={
                        "workflow": {
                            "type": "string",
                            "enum": ["lab_results", "prescription_status"],
                            "description": "Workflow: lab_results (test results) or prescription_status (refills/medications)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief context for the next workflow",
                        },
                    },
                    required=["workflow", "reason"],
                    handler=self._route_to_workflow_handler,
                ),
                FlowsFunctionSchema(
                    name="correct_info",
                    description="Patient wants to correct information. For appointment_date, ONLY accept dates after TODAY shown above. If they suggest a past date, politely clarify the correct year.",
                    properties={
                        "field": {
                            "type": "string",
                            "description": "Field to correct: 'first_name', 'last_name', 'phone_number', 'date_of_birth', 'email', 'appointment_date', or 'appointment_time'",
                        },
                        "new_value": {
                            "type": "string",
                            "description": "The corrected value (appointment_date must be after TODAY)",
                        },
                    },
                    required=["field", "new_value"],
                    handler=self._correct_info_handler,
                ),
                FlowsFunctionSchema(
                    name="continue_via_text",
                    description="Patient wants to continue conversation over text/SMS. Call when they say 'yes' to text offer, or ask to 'text me', 'send me a text', etc.",
                    properties={},
                    required=[],
                    handler=self._offer_text_continuation_handler,
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
        """Ask patient to confirm they want to speak with a staff member."""
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
                    "content": """Transfer the patient to staff.

CRITICAL: If patient has ALREADY confirmed transfer (said yes, please, transfer me, etc.), call dial_staff IMMEDIATELY. Do NOT ask again.

If they haven't confirmed yet, ask once: "Would you like me to transfer you?"
- Positive response → call dial_staff NOW
- Negative response → call set_new_patient or set_returning_patient

NEVER say "transferring" or "connecting" without calling dial_staff in the same turn.
ONE response max, then call the function.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="dial_staff",
                    description="Transfer to staff when they confirm.",
                    properties={},
                    required=[],
                    handler=self._dial_staff_handler,
                ),
                FlowsFunctionSchema(
                    name="set_new_patient",
                    description="Call when patient says they're new and wants to continue scheduling.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if mentioned. Omit or leave empty if not mentioned.",
                        },
                        "visit_reason": {
                            "type": "string",
                            "description": "Reason for visit if mentioned, or empty string.",
                        },
                    },
                    required=[],
                    handler=self._set_new_patient_handler,
                ),
                FlowsFunctionSchema(
                    name="set_returning_patient",
                    description="Call when patient says they've been here before and wants to continue scheduling.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "Patient's first name if mentioned. Omit or leave empty if not mentioned.",
                        },
                        "visit_reason": {
                            "type": "string",
                            "description": "Reason for visit if mentioned, or empty string.",
                        },
                    },
                    required=[],
                    handler=self._set_returning_patient_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_transfer_initiated_node(self) -> NodeConfig:
        """Node shown while transfer is in progress."""
        return NodeConfig(
            name="transfer_initiated",
            task_messages=[],
            functions=[],
            pre_actions=[
                {"type": "tts_say", "text": "Transferring you now, please hold."}
            ],
            post_actions=[{"type": "end_conversation"}],
        )

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
                    "content": """The transfer didn't go through. Apologize and offer alternatives.

If caller wants to try the transfer again:
→ Call retry_transfer

If caller says goodbye or wants to end call:
→ Call end_call

If caller wants to continue scheduling:
→ Answer their question, then ask if there's anything else""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="retry_transfer",
                    description="""Retry the failed transfer.

WHEN TO USE: Caller wants to try the transfer again.
RESULT: Attempts SIP transfer again.""",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call gracefully.

WHEN TO USE: Caller says goodbye or confirms no more questions.
RESULT: Ends the call.""",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
            ],
            respond_immediately=True,
            pre_actions=[
                {"type": "tts_say", "text": "I apologize, the transfer didn't go through."}
            ],
        )

    # ========== Returning Patient Lookup Nodes ==========

    def create_returning_patient_lookup_node(self) -> NodeConfig:
        """Ask returning patient for phone number to look up their record."""
        return NodeConfig(
            name="returning_patient_lookup",
            task_messages=[
                {
                    "role": "system",
                    "content": """Ask for their phone number to pull up their record: "Let me pull up your file. What's the phone number on your account?"

Once they provide a phone number, call lookup_by_phone with the digits.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="lookup_by_phone",
                    description="Look up patient record by phone number.",
                    properties={
                        "phone_number": {
                            "type": "string",
                            "description": "Patient's phone number (digits only, e.g., '5551234567')",
                        },
                    },
                    required=["phone_number"],
                    handler=self._lookup_by_phone_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_returning_patient_verify_dob_node(self) -> NodeConfig:
        """Ask returning patient to verify DOB before confirming identity."""
        return NodeConfig(
            name="returning_patient_verify_dob",
            task_messages=[
                {
                    "role": "system",
                    "content": """Found a record. Ask for date of birth to verify: "I found a record. Can you confirm your date of birth?"

Once they provide DOB, call verify_dob.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="verify_dob",
                    description="Verify patient identity by date of birth.",
                    properties={
                        "date_of_birth": {
                            "type": "string",
                            "description": "Patient's date of birth in natural format (e.g., 'May 18, 1975', 'January 5th 1990')",
                        },
                    },
                    required=["date_of_birth"],
                    handler=self._verify_dob_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_returning_patient_not_found_node(self) -> NodeConfig:
        """Patient record not found - transfer to staff."""
        return NodeConfig(
            name="returning_patient_not_found",
            task_messages=[],
            functions=[],
            pre_actions=[
                {"type": "tts_say", "text": "I couldn't find your record in our system. Let me connect you with a colleague who can help. One moment."}
            ],
            post_actions=[{"type": "end_conversation"}],
        )

    # ========== Function Handlers ==========

    def _store_volunteered_info(self, args: Dict[str, Any], flow_manager: FlowManager) -> list[str]:
        """Store any volunteered info in state. Returns list of captured fields."""
        captured = []

        for field in ["first_name", "last_name", "phone_number", "email"]:
            value = args.get(field, "").strip()
            if value and value.lower() not in ["unknown", ""]:
                flow_manager.state[field] = value
                captured.append(field)

        # Handle date_of_birth with parsing
        dob = args.get("date_of_birth", "").strip()
        if dob:
            parsed_dob = parse_natural_date(dob) or dob
            flow_manager.state["date_of_birth"] = parsed_dob
            captured.append("date_of_birth")

        # Handle visit_reason separately (maps to appointment_reason)
        visit_reason = args.get("visit_reason", "").strip()
        if visit_reason:
            flow_manager.state["appointment_reason"] = visit_reason
            captured.append("visit_reason")

        return captured

    async def _set_new_patient_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Patient is new. Store any volunteered info."""
        flow_manager.state["appointment_type"] = "New Patient"
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: New Patient - captured: {captured if captured else 'none'}")

        # Skip visit_reason node if already provided
        if flow_manager.state.get("appointment_reason"):
            return None, self.create_scheduling_node()
        return None, self.create_visit_reason_node()

    async def _set_returning_patient_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Patient is returning. Go to lookup flow to verify identity."""
        flow_manager.state["appointment_type"] = "Returning Patient"
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: Returning Patient - captured: {captured if captured else 'none'}")

        # Check if caller is already verified (e.g., handed off from lab_results)
        if flow_manager.state.get("identity_verified"):
            # Copy verified patient info from state if available
            patient_name = flow_manager.state.get("patient_name", "")
            if patient_name:
                # Parse "Last, First" format if present
                if "," in patient_name:
                    parts = [p.strip() for p in patient_name.split(",")]
                    if len(parts) == 2:
                        flow_manager.state["last_name"] = parts[0]
                        flow_manager.state["first_name"] = parts[1]
                else:
                    # Assume "First Last" format
                    parts = patient_name.split()
                    if len(parts) >= 2:
                        flow_manager.state["first_name"] = parts[0]
                        flow_manager.state["last_name"] = " ".join(parts[1:])

            # Copy other verified fields
            for field in ["phone_number", "date_of_birth", "email"]:
                if flow_manager.state.get(field):
                    pass  # Already in state from lab_results verification

            first_name = flow_manager.state.get("first_name", "")
            logger.info(f"Flow: Caller already verified as {first_name}, skipping lookup")

            # Skip visit_reason if already provided, otherwise ask
            if flow_manager.state.get("appointment_reason"):
                return f"Great, {first_name}! Let me help you schedule that.", self.create_scheduling_node()
            return None, self.create_visit_reason_node()

        # Go to lookup flow to verify patient identity
        return None, self.create_returning_patient_lookup_node()

    async def _save_visit_reason_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save visit reason and proceed to scheduling."""
        appointment_reason = args.get("reason", "").strip()
        flow_manager.state["appointment_reason"] = appointment_reason
        logger.info(f"Flow: Visit reason - {appointment_reason}")
        return "Let's get you scheduled.", self.create_scheduling_node()

    # ========== Returning Patient Lookup Handlers ==========

    async def _lookup_by_phone_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Look up patient by phone number."""
        phone_number = args.get("phone_number", "").strip()
        # Normalize to digits only
        phone_digits = ''.join(c for c in phone_number if c.isdigit())
        logger.info(f"Flow: Looking up patient by phone: {phone_digits[-4:] if len(phone_digits) >= 4 else '***'}")

        db = get_async_patient_db()
        patient_record = await db.find_patient_by_phone(phone_digits, self.organization_id)

        if patient_record:
            # Store the found record temporarily for DOB verification
            # Use underscore prefix to indicate these are internal lookup fields
            flow_manager.state["_lookup_record"] = {
                "first_name": patient_record.get("first_name", ""),
                "last_name": patient_record.get("last_name", ""),
                "phone_number": patient_record.get("phone_number", ""),
                "date_of_birth": patient_record.get("date_of_birth", ""),
                "email": patient_record.get("email", ""),
            }
            logger.info(f"Flow: Found patient record, requesting DOB verification")
            return None, self.create_returning_patient_verify_dob_node()
        else:
            logger.info(f"Flow: No patient found for phone {phone_digits[-4:] if len(phone_digits) >= 4 else '***'}")
            # Initiate transfer to staff
            await self._initiate_staff_transfer(flow_manager)
            return None, self.create_returning_patient_not_found_node()

    async def _verify_dob_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Verify patient identity by comparing DOB."""
        provided_dob = args.get("date_of_birth", "").strip()
        # Normalize to ISO format for comparison
        provided_dob_normalized = parse_natural_date(provided_dob)

        lookup_record = flow_manager.state.get("_lookup_record", {})
        stored_dob = lookup_record.get("date_of_birth", "")

        logger.info(f"Flow: Verifying DOB - provided: {provided_dob_normalized}, stored: {stored_dob}")

        if provided_dob_normalized and provided_dob_normalized == stored_dob:
            # DOB matches - copy verified info to state
            first_name = lookup_record.get("first_name", "")
            flow_manager.state["first_name"] = first_name
            flow_manager.state["last_name"] = lookup_record.get("last_name", "")
            flow_manager.state["phone_number"] = lookup_record.get("phone_number", "")
            flow_manager.state["date_of_birth"] = stored_dob
            flow_manager.state["email"] = lookup_record.get("email", "")

            # Clean up temporary lookup state
            del flow_manager.state["_lookup_record"]

            logger.info(f"Flow: DOB verified for {first_name}")

            # Skip visit_reason if already provided, otherwise ask
            if flow_manager.state.get("appointment_reason"):
                return f"Welcome back, {first_name}!", self.create_scheduling_node()
            return f"Welcome back, {first_name}!", self.create_visit_reason_node()
        else:
            # DOB doesn't match - transfer to staff
            logger.warning(f"Flow: DOB mismatch - transferring to staff")
            # Clean up temporary lookup state
            if "_lookup_record" in flow_manager.state:
                del flow_manager.state["_lookup_record"]
            await self._initiate_staff_transfer(flow_manager)
            return "That doesn't match what I have on file. Let me connect you with a colleague who can help.", self.create_returning_patient_not_found_node()

    async def _initiate_staff_transfer(self, flow_manager: FlowManager) -> None:
        """Initiate cold transfer to staff (helper for returning patient not found)."""
        staff_number = self.cold_transfer_config.get("staff_number")
        if staff_number and self.transport:
            try:
                if self.pipeline:
                    self.pipeline.transfer_in_progress = True
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"Flow: Staff transfer initiated to {staff_number}")
            except Exception as e:
                logger.error(f"Flow: Staff transfer failed: {e}")

    async def _capture_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Capture volunteered patient info and stay in scheduling node."""
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: Captured volunteered info: {captured if captured else 'none'}")
        # Return minimal response - if called with schedule_appointment, avoid duplication
        return "Got it.", self.create_scheduling_node()

    async def _schedule_appointment_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Save appointment date and time with validation against available slots."""
        raw_date = args.get("appointment_date", "").strip()
        raw_time = args.get("appointment_time", "").strip()

        # Capture any volunteered patient info
        self._store_volunteered_info(args, flow_manager)

        # Normalize to ISO format for storage
        appointment_date = parse_natural_date(raw_date) or raw_date
        appointment_time = parse_natural_time(raw_time) or raw_time

        # Validate against available slots
        available_slots = flow_manager.state.get("available_slots", [])
        slot_valid = False
        for slot in available_slots:
            # Check if the date and time match any available slot
            slot_lower = slot.lower()
            # Parse the slot date for comparison
            if appointment_date:
                try:
                    scheduled = date.fromisoformat(appointment_date)
                    slot_date_str = scheduled.strftime("%B %d").lower()  # "december 12"
                    time_lower = raw_time.lower()
                    if slot_date_str in slot_lower and time_lower in slot_lower:
                        slot_valid = True
                        break
                except ValueError:
                    pass

        if not slot_valid:
            slots_text = " or ".join(available_slots)
            logger.warning(f"Flow: Rejected invalid slot: {raw_date} at {raw_time}")
            return f"That slot isn't available. Please choose from: {slots_text}.", self.create_scheduling_node()

        flow_manager.state["appointment_date"] = appointment_date
        flow_manager.state["appointment_time"] = appointment_time
        logger.info(f"Flow: Scheduled {raw_date} → {appointment_date} at {raw_time} → {appointment_time}")

        # Check if we already have all required patient info (e.g., verified returning patient)
        required_fields = ["first_name", "last_name", "phone_number", "date_of_birth", "email"]
        has_all_info = all(flow_manager.state.get(field) for field in required_fields)

        if has_all_info:
            # Skip collect_info for verified returning patients
            logger.info("Flow: All patient info already present, skipping to confirmation")
            return "Perfect! Let me confirm your appointment.", self.create_confirmation_node()

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
        """Correct a piece of information with validation."""
        field = args.get("field", "").strip()
        new_value = args.get("new_value", "").strip()

        # Validate appointment_date is in the future
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
            logger.exception("Error in end_call_handler")

            if patient_id and db:
                try:
                    await db.update_call_status(patient_id, "Failed", self.organization_id)
                except Exception as db_error:
                    logger.error(f"Failed to update status to Failed: {db_error}")

        return None, self._create_end_node()

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Transfer to staff. If urgent=true or patient_confirmed=true, transfer immediately."""
        urgent = args.get("urgent", False)
        patient_confirmed = args.get("patient_confirmed", False)
        reason = args.get("reason", "general inquiry")

        # Store reason for potential retry
        flow_manager.state["transfer_reason"] = reason

        logger.info(f"Flow: Staff transfer requested - reason: {reason}, urgent: {urgent}, confirmed: {patient_confirmed}")

        if urgent or patient_confirmed:
            # Immediate transfer - no confirmation needed
            staff_number = self.cold_transfer_config.get("staff_number")

            if not staff_number:
                logger.warning("Cold transfer requested but no staff_number configured")
                return None, self.create_transfer_failed_node()

            try:
                if self.pipeline:
                    self.pipeline.transfer_in_progress = True
                if self.transport:
                    await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                    logger.info(f"SIP call transfer initiated: {staff_number}")

                # Update call status
                try:
                    patient_id = self.patient_data.get("patient_id")
                    if patient_id:
                        db = get_async_patient_db()
                        await db.update_call_status(patient_id, "Transferred", self.organization_id)
                except Exception as e:
                    logger.error(f"Error updating call status: {e}")

                return None, self.create_transfer_initiated_node()
            except Exception as e:
                logger.exception("Cold transfer failed")
                if self.pipeline:
                    self.pipeline.transfer_in_progress = False
                return None, self.create_transfer_failed_node()

        # Non-urgent and not confirmed: ask for confirmation
        logger.info("Flow: transitioning to staff_confirmation")
        return None, self.create_staff_confirmation_node()

    async def _dial_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Cold transfer to staff after confirmation."""
        staff_number = self.cold_transfer_config.get("staff_number")

        if not staff_number:
            logger.warning("Cold transfer requested but no staff_number configured")
            return None, self.create_transfer_failed_node()

        try:
            logger.info(f"Cold transfer initiated to: {staff_number}")

            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"SIP call transfer initiated: {staff_number}")

            # Update call status
            try:
                patient_id = self.patient_data.get("patient_id")
                if patient_id:
                    db = get_async_patient_db()
                    await db.update_call_status(patient_id, "Transferred", self.organization_id)
            except Exception as e:
                logger.error(f"Error updating call status: {e}")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception("Cold transfer failed")

            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            return None, self.create_transfer_failed_node()

    async def _retry_transfer_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Retry a failed SIP transfer."""
        logger.info("Flow: Retrying SIP transfer")

        staff_number = self.cold_transfer_config.get("staff_number")

        if not staff_number:
            logger.warning("Retry transfer requested but no staff_number configured")
            return None, self.create_transfer_failed_node()

        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"SIP call transfer retry initiated: {staff_number}")

            # Update call status
            try:
                patient_id = self.patient_data.get("patient_id")
                if patient_id:
                    db = get_async_patient_db()
                    await db.update_call_status(patient_id, "Transferred", self.organization_id)
            except Exception as e:
                logger.error(f"Error updating call status: {e}")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception("Cold transfer retry failed")

            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            return None, self.create_transfer_failed_node()

    async def _return_to_conversation_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Return to the previous conversation node if they decline transfer."""
        logger.info("Flow: returning to confirmation node")
        return "No problem, let me continue helping you.", self.create_confirmation_node()

    async def _route_to_workflow_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Route to an AI workflow (same call, no phone transfer)."""
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")

        flow_manager.state["routed_to"] = f"{workflow} (AI)"

        logger.info(f"Flow: Routing to {workflow} workflow - reason: {reason}")

        if workflow == "lab_results":
            return await self._handoff_to_lab_results(flow_manager, reason)
        elif workflow == "prescription_status":
            return await self._handoff_to_prescription_status(flow_manager, reason)
        else:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

    async def _handoff_to_lab_results(
        self, flow_manager: FlowManager, reason: str
    ) -> tuple[str, NodeConfig]:
        """Hand off to LabResultsFlow with gathered context."""
        from clients.demo_clinic_alpha.lab_results.flow_definition import LabResultsFlow

        lab_results_flow = LabResultsFlow(
            patient_data=self.patient_data,
            flow_manager=flow_manager,
            main_llm=self.main_llm,
            context_aggregator=self.context_aggregator,
            transport=self.transport,
            pipeline=self.pipeline,
            organization_id=self.organization_id,
            cold_transfer_config=self.cold_transfer_config,
        )

        logger.info(f"Flow: Handing off to LabResultsFlow with context: {reason}")

        # Use handoff entry point with context (no greeting, context-aware)
        return None, lab_results_flow.create_handoff_entry_node(context=reason)

    async def _handoff_to_prescription_status(
        self, flow_manager: FlowManager, reason: str
    ) -> tuple[str, NodeConfig]:
        """Hand off to PrescriptionStatusFlow with gathered context."""
        from clients.demo_clinic_alpha.prescription_status.flow_definition import PrescriptionStatusFlow

        prescription_flow = PrescriptionStatusFlow(
            patient_data=self.patient_data,
            flow_manager=flow_manager,
            main_llm=self.main_llm,
            context_aggregator=self.context_aggregator,
            transport=self.transport,
            pipeline=self.pipeline,
            organization_id=self.organization_id,
            cold_transfer_config=self.cold_transfer_config,
        )

        logger.info(f"Flow: Handing off to PrescriptionStatusFlow with context: {reason}")

        # Use handoff entry point with context (no greeting, context-aware)
        return None, prescription_flow.create_handoff_entry_node(context=reason)

    def _get_request_staff_function(self) -> FlowsFunctionSchema:
        """Return the request_staff function schema for use in multiple nodes."""
        return FlowsFunctionSchema(
            name="request_staff",
            description="""Transfer call to human staff member.

WHEN TO USE:
- Caller needs help with something other than scheduling
- Caller has medical emergency or urgent symptoms
- Caller explicitly asks for a human
- Caller is frustrated

EXAMPLES:
- Medical emergency (pain, swelling) → call with urgent=true
- "I want to talk to a person" → call with patient_confirmed=true
- Billing question → call with reason="billing"
- Cancel/reschedule existing → call with reason="reschedule" """,
            properties={
                "urgent": {
                    "type": "boolean",
                    "description": "Set true for urgent requests that need immediate attention (medical emergencies, pain, swelling). Transfers immediately.",
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

    # ========== Text Conversation Handlers ==========

    async def _offer_text_continuation_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Patient wants to continue conversation over text. Save state and queue SMS."""
        state = flow_manager.state
        phone_number = state.get("phone_number", "")

        if not phone_number:
            logger.warning("Text continuation requested but no phone number in state")
            return "I don't have a phone number on file. Let me confirm your number first.", self.create_confirmation_node()

        # Build context to carry over to text conversation
        text_context = {
            "first_name": state.get("first_name"),
            "last_name": state.get("last_name"),
            "phone_number": phone_number,
            "email": state.get("email"),
            "date_of_birth": state.get("date_of_birth"),
            "appointment_date": state.get("appointment_date"),
            "appointment_time": state.get("appointment_time"),
            "appointment_type": state.get("appointment_type"),
            "appointment_reason": state.get("appointment_reason"),
        }

        # Create text conversation instance
        text_conv = TextConversation(
            patient_id=self.patient_data.get("patient_id", ""),
            organization_id=self.organization_id,
            organization_name=self.organization_name,
            initial_context=text_context,
        )

        # Get the handoff message
        handoff_message = text_conv.get_handoff_message()

        # Save text conversation state to database for later retrieval
        try:
            db = get_async_patient_db()
            patient_id = self.patient_data.get("patient_id")
            if patient_id:
                await db.update_patient(
                    patient_id,
                    {
                        "text_conversation_enabled": True,
                        "text_conversation_state": text_conv.to_dict(),
                        "text_handoff_message": handoff_message,
                    },
                    self.organization_id,
                )
                logger.info(f"Text continuation enabled for patient {patient_id}")

                # TODO: Queue SMS via Twilio/your SMS provider
                # await sms_service.send(phone_number, handoff_message)
                logger.info(f"SMS would be sent to {phone_number[-4:]}: {handoff_message[:50]}...")

        except Exception as e:
            logger.error(f"Error enabling text continuation: {e}")
            return "I'm having trouble setting that up. You can always call us back!", self.create_confirmation_node()

        return "I'll send you a text right now. You can reply anytime with questions!", self._create_end_node()