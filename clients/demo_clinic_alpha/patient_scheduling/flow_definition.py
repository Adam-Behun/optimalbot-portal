import os
from datetime import date, timedelta
from typing import Any, Dict

from openai import AsyncOpenAI
from pipecat_flows import FlowManager, FlowsFunctionSchema, NodeConfig
from loguru import logger

from backend.models import get_async_patient_db
from backend.sessions import get_async_session_db
from backend.utils import parse_natural_date, parse_natural_time
from clients.demo_clinic_alpha.dialin_base_flow import DialinBaseFlow
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
        await client.chat.completions.create(model="gpt-4o-mini", messages=messages, max_tokens=1)
        logger.info("OpenAI cache warmed with scheduling prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class PatientSchedulingFlow(DialinBaseFlow):
    ALLOWS_NEW_PATIENTS = True
    CONFIRM_BEFORE_TRANSFER = True
    WORKFLOW_FLOWS = {
        "lab_results": ("clients.demo_clinic_alpha.lab_results.flow_definition", "LabResultsFlow"),
        "prescription_status": ("clients.demo_clinic_alpha.prescription_status.flow_definition", "PrescriptionStatusFlow"),
    }

    PLACEHOLDER_VALUES = {"new", "patient", "unknown", "none", "n/a", "na", "not yet collected", "not provided", "not available", "tbd", "pending", "null", "undefined"}
    REQUIRED_FIELDS = ["first_name", "last_name", "phone_number", "date_of_birth", "email"]
    PATIENT_INFO_PROPS = {
        "first_name": {"type": "string", "description": "First name if mentioned."},
        "last_name": {"type": "string", "description": "Last name if mentioned."},
        "phone_number": {"type": "string", "description": "Phone if mentioned (digits only)."},
        "email": {"type": "string", "description": "Email if mentioned."},
    }

    def __init__(self, call_data: Dict[str, Any], session_id: str, flow_manager: FlowManager, main_llm,
                 context_aggregator=None, transport=None, pipeline=None, organization_id: str = None,
                 cold_transfer_config: Dict[str, Any] = None):
        self.today = date.today()
        self.available_slots = self._generate_available_slots()
        super().__init__(call_data, session_id, flow_manager, main_llm, context_aggregator, transport,
                         pipeline, organization_id, cold_transfer_config)

    def _init_domain_state(self):
        state = self.flow_manager.state
        state["today"] = self.today.strftime("%B %d, %Y")
        state["available_slots"] = self.available_slots

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

    def _get_workflow_type(self) -> str:
        return "patient_scheduling"

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

    def _extract_lookup_record(self, patient: dict) -> dict:
        return {f: patient.get(f, "") for f in self.REQUIRED_FIELDS + ["patient_id"]}

    def _populate_domain_state(self, flow_manager: FlowManager, lookup: dict):
        pass

    def _route_after_verification(self, flow_manager: FlowManager) -> NodeConfig:
        if flow_manager.state.get("appointment_reason"):
            return self.create_scheduling_node()
        return self.create_visit_reason_node()

    def _get_verification_greeting(self, first_name: str) -> str | None:
        return f"Welcome back, {first_name}!" if first_name else "Welcome back!"

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

    def _end_node(self) -> NodeConfig:
        return NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Thank the patient and say goodbye."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )

    def get_initial_node(self) -> NodeConfig:
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
                self._request_staff_schema(),
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
            return self.create_patient_lookup_node()
        else:
            self.flow_manager.state["appointment_type"] = "New Patient"
            logger.info("Flow: Handoff entry - new patient, context stored")
            if self.flow_manager.state.get("appointment_reason"):
                return self.create_scheduling_node()
            return self.create_visit_reason_node()

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
                self._request_staff_schema(),
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
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

    def create_slot_selection_node(self) -> NodeConfig:
        return self.create_scheduling_node()

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
                self._request_staff_schema(),
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
                self._route_to_workflow_schema(),
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
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

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
                self._route_to_workflow_schema(),
            ],
            respond_immediately=True,
        )

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
        return None, self.create_patient_lookup_node()

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
        patient_id = flow_manager.state.get("patient_id")
        if patient_id:
            await self._try_db_update(patient_id, "update_patient", {
                "appointment_date": appointment_date,
                "appointment_time": appointment_time,
                "appointment_slot": matched_slot,
                "appointment_reason": flow_manager.state.get("appointment_reason", ""),
            }, error_msg="Error saving appointment")
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
            update = {**values, "patient_name": f"{values['last_name']}, {values['first_name']}"}
            update.update({k: flow_manager.state.get(k) for k in ["appointment_date", "appointment_time", "appointment_type", "appointment_reason"]})
            await self._try_db_update(patient_id, "update_patient", update, error_msg="Error saving patient info")
        else:
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
                session_db = get_async_session_db()
                await session_db.update_session(self.session_id, {"patient_id": patient_id}, self.organization_id)
            else:
                logger.error("Flow: Failed to create patient record")
        return "Thank you! Let me confirm all the details.", self.create_confirmation_node()

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

    async def _dial_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        return await self._initiate_sip_transfer(flow_manager)

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
