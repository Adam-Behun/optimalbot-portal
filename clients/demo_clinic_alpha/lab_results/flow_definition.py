import os
from typing import Dict, Any

from openai import AsyncOpenAI
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger

from backend.models import get_async_patient_db
from backend.utils import parse_natural_date
from handlers.transcript import save_transcript_to_db


class _MockFlowManager:
    def __init__(self):
        self.state = {}


async def warmup_openai(patient_data: dict = None):
    try:
        patient_data = patient_data or {"organization_name": "Demo Clinic Alpha"}
        flow = LabResultsFlow(
            patient_data=patient_data,
            flow_manager=_MockFlowManager(),
            main_llm=None,
        )
        greeting_node = flow.create_greeting_node()

        messages = [{"role": m["role"], "content": m["content"]} for m in (greeting_node.role_messages or []) + (greeting_node.task_messages or [])]
        messages.append({"role": "user", "content": "Hi, I'm calling about my lab results"})

        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=1,
        )
        logger.info("OpenAI cache warmed with lab_results prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class LabResultsFlow:

    # ==================== Class Constants ====================

    WORKFLOW_FLOWS = {
        "scheduling": ("clients.demo_clinic_alpha.patient_scheduling.flow_definition", "PatientSchedulingFlow"),
        "prescription_status": ("clients.demo_clinic_alpha.prescription_status.flow_definition", "PrescriptionStatusFlow"),
    }

    # ==================== Initialization ====================

    def __init__(
        self,
        patient_data: Dict[str, Any],
        flow_manager: FlowManager,
        main_llm=None,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
        cold_transfer_config: Dict[str, Any] = None,
    ):
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.patient_data = patient_data
        self.organization_name = patient_data.get("organization_name", "Demo Clinic Alpha")
        self.cold_transfer_config = cold_transfer_config or {}
        self._init_state()

    def _init_state(self):
        for field in ["patient_id", "patient_name", "first_name", "last_name",
                      "date_of_birth", "medical_record_number", "phone_number"]:
            self.flow_manager.state[field] = (
                self.flow_manager.state.get(field) or self.patient_data.get(field, "")
            )

        for field in ["test_type", "test_date", "ordering_physician", "results_status", "results_summary"]:
            self.flow_manager.state[field] = self.patient_data.get(field, "")

        self.flow_manager.state["provider_review_required"] = self.patient_data.get("provider_review_required", False)
        self.flow_manager.state["callback_timeframe"] = self.patient_data.get("callback_timeframe", "24 to 48 hours")
        self.flow_manager.state.setdefault("identity_verified", False)
        self.flow_manager.state.setdefault("results_communicated", False)

    # ==================== Helpers: Normalization ====================

    def _normalize_name(self, name: str) -> str:
        name = name.strip().lower()
        if "," in name:
            parts = [p.strip() for p in name.split(",")]
            if len(parts) == 2:
                return f"{parts[1]} {parts[0]}"
        return name

    def _normalize_dob(self, dob: str) -> str | None:
        if not dob:
            return None
        return parse_natural_date(dob.strip()) or dob.strip()

    def _normalize_phone(self, phone: str) -> str:
        return ''.join(c for c in phone if c.isdigit())

    def _phone_last4(self, phone: str) -> str:
        return phone[-4:] if len(phone) >= 4 else ""

    async def _try_db_update(self, patient_id: str, method: str, *args, error_msg: str = "DB update error"):
        if not patient_id:
            return
        try:
            db = get_async_patient_db()
            await getattr(db, method)(patient_id, *args, self.organization_id)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")

    async def _update_phone_number(self, new_number: str, flow_manager: FlowManager) -> str:
        new_number_digits = self._normalize_phone(new_number)
        flow_manager.state["phone_number"] = new_number_digits
        logger.info(f"Flow: Callback number updated to {self._phone_last4(new_number_digits)}")
        patient_id = flow_manager.state.get("patient_id")
        await self._try_db_update(patient_id, "update_patient", {"caller_phone_number": new_number_digits}, error_msg="Error updating callback number")
        return new_number_digits

    # ==================== Helpers: Prompts ====================

    def _get_global_instructions(self) -> str:
        return f"""You are Jamie, a friendly assistant for {self.organization_name}.

# Voice Conversation Style
You are on a phone call with a patient. Your responses will be converted to speech:
- Speak naturally and warmly, like a helpful clinic staff member
- Keep responses concise—one or two sentences is usually enough
- Use natural acknowledgments: "Of course", "I understand", "Let me check that for you"
- NEVER use bullet points, numbered lists, asterisks, or markdown formatting
- If asked to repeat, SHORTEN your response each time

# Handling Speech Recognition
The input is transcribed from speech and may contain errors:
- Silently correct obvious transcription mistakes based on context
- "march twenty second" means "March 22nd"
- If truly unclear, ask naturally: "Sorry, I didn't catch that"

# HIPAA Compliance
- You MUST verify patient identity before discussing ANY health information. This step is important.
- Never share lab results with unverified callers
- If verification fails, do not provide any lab information

# Guardrails
- You READ lab results; you do NOT interpret them or add meaning beyond what's written
- After reading results, if caller asks what they mean → "Your doctor can give you the best guidance on what this means for you."
- NEVER share results if provider_review_required is True—only explain the doctor will call
- If you don't have information, say so honestly
- Stay on topic: lab results inquiries only
- If caller is frustrated or asks for a human, transfer them"""

    # ==================== Helpers: Function Schemas ====================

    def _end_call_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="end_call",
            description="End the call when caller says goodbye or confirms no more questions.",
            properties={},
            required=[],
            handler=self._end_call_handler,
        )

    def _request_staff_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="request_staff",
            description="Transfer to human staff. Use for explicit human requests, billing, or unhandled issues.",
            properties={"reason": {"type": "string", "description": "Brief reason for transfer"}},
            required=[],
            handler=self._request_staff_handler,
        )

    def _route_to_workflow_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="route_to_workflow",
            description="Route to AI workflow. Caller is already verified - context carries through.",
            properties={
                "workflow": {"type": "string", "enum": ["scheduling", "prescription_status"]},
                "reason": {"type": "string", "description": "Brief context for the next workflow"},
            },
            required=["workflow", "reason"],
            handler=self._route_to_workflow_handler,
        )

    # ==================== Node Creators: Entry Points ====================

    def create_greeting_node(self) -> NodeConfig:
        return NodeConfig(
            name="greeting",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """# Goal
Determine what the caller needs and route appropriately.

# Scenario Handling
If caller asks about LAB RESULTS, TEST RESULTS, or BLOOD WORK:
→ Respond naturally and briefly: "Sounds good!" or "Of course!" or "Sure thing!"
→ Call proceed_to_verification immediately (the verification node will ask for their name)
→ Do NOT say "I need to verify your identity" - that sounds robotic

If caller is frustrated about lab results:
→ Acknowledge briefly: "I understand, let me help you with that."
→ Call proceed_to_verification immediately

If caller EXPLICITLY asks for a human/person/transfer:
→ "Let me connect you with someone who can help."
→ Call request_staff

If caller needs something ELSE (appointments, billing, prescriptions, etc.):
→ Say "Let me connect you with someone who can help with that."
→ Call request_staff

# Example Flow
Caller: "I'm calling to check on my lab results."
→ Say "Sounds good!" and call proceed_to_verification

Caller: "Hi, I need my blood work results."
→ Say "Of course!" and call proceed_to_verification

Caller frustrated: "I've called THREE times about my blood work!"
→ Say "I understand, let me help you with that." and call proceed_to_verification

Caller: "Can I speak to a person?"
→ "Let me connect you with someone."
→ Call request_staff

# Guardrails
- Do NOT ask for any personal information yet - the verification node handles that
- Do NOT discuss lab results until identity is verified
- Do NOT say "First, I need to verify your identity" - sounds unnatural
- Route to verification as soon as caller mentions lab/test results
- Frustrated callers asking about lab results should STILL go through verification (not transfer)
- Only transfer if caller explicitly asks for a human

# Error Handling
If you miss what the caller said:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never guess what they need""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_verification",
                    description="Proceed to identity verification. Use when caller asks about lab/test results.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_verification_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": f"Hello! Thank you for calling {self.organization_name}. How can I help you today?"}],
        )

    def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        self.flow_manager.state["test_type"] = "biopsy" if "biopsy" in context.lower() else ""
        self.flow_manager.state["caller_anxious"] = "anxious" in context.lower() or "worried" in context.lower()
        logger.info("Flow: Handoff entry - context stored, proceeding to verification")
        return self.create_verification_node()

    # ==================== Node Creators: Main Flow ====================

    def create_verification_node(self) -> NodeConfig:
        state = self.flow_manager.state
        stored_name = state.get("patient_name", "")
        stored_dob = state.get("date_of_birth", "")

        return NodeConfig(
            name="verification",
            task_messages=[{
                "role": "system",
                "content": f"""# YOUR FIRST MESSAGE
Start by asking for the caller's name. Do NOT say:
- "I've initiated the process" or "I've routed your request"
- "Please hold" or "one moment"
- "Let me connect you" or "someone will help you"

Just ask: "I can help you with that. Can I have your full name?"

# CRITICAL RULE: CALL verify_identity AS SOON AS YOU HAVE NAME + DOB
The moment you have both name and date of birth, YOU MUST call verify_identity.

WRONG: Say "Let me check your records" and stop (NEVER DO THIS)
RIGHT: Call verify_identity(name="David Chen", date_of_birth="November 2, 1958") immediately

If caller is anxious while you have their info, call verify_identity FIRST, then you can help them.

# Patient Record on File
- Name: {stored_name}
- Date of Birth: {stored_dob}

# Example Flow
You: "Can I have your first and last name?"
Caller: "David Chen"
You: "Thanks, David. And your date of birth?"
Caller: "November 2nd, 1958"
→ Call verify_identity(name="David Chen", date_of_birth="November 2, 1958")

# Anxious Caller Example
You: "And your date of birth?"
Caller: "November 2nd, 1958... but please, I need to know!"
→ STILL call verify_identity(name="David Chen", date_of_birth="November 2, 1958") FIRST
→ The function will handle the next step - don't worry about their anxiety until after verification

# Data Normalization
**Dates** (spoken → written):
- "march twenty second nineteen seventy eight" → "March 22, 1978"
- "three twenty two seventy eight" → "March 22, 1978"
- "oh three twenty two nineteen seventy eight" → "March 22, 1978"

Always normalize dates before calling verify_identity.

# Guardrails
- Collect BOTH name AND date of birth before calling verify_identity. This step is important.
- Do NOT reveal any patient information during verification
- Do NOT say whether the name or DOB matches until both are collected
- Be patient if caller needs to repeat information
- ONLY call ONE function per turn - either verify_identity OR request_staff, never both
- Do NOT call request_staff just because caller sounds anxious, impatient, or says "urgent" - verify FIRST, then help them
- Phrases like "I don't want to wait" or "this is urgent" are NOT requests for human transfer - they're expressing concern
- ONLY call request_staff if caller explicitly says "transfer me", "I want to talk to a person", or "give me a human"

# When to use each function
- verify_identity: After you have BOTH name AND date of birth → always try this first
- request_staff: ONLY if caller explicitly refuses to verify AND asks for human transfer

# Error Handling
If you miss information:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never guess or make up values
- If caller is unclear, ask for clarification: "Could you spell that for me?" """,
            }],
            functions=[
                FlowsFunctionSchema(
                    name="verify_identity",
                    description="Verify caller identity. Call immediately after collecting name AND date of birth.",
                    properties={
                        "name": {"type": "string", "description": "Caller's full name as stated (first and last)"},
                        "date_of_birth": {"type": "string", "description": "Caller's date of birth in natural format (e.g., 'March 22, 1978')"},
                    },
                    required=["name", "date_of_birth"],
                    handler=self._verify_identity_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

    def create_results_node(self) -> NodeConfig:
        state = self.flow_manager.state
        test_type = state.get("test_type", "lab test")
        ordering_physician = state.get("ordering_physician", "your doctor")
        results_status = state.get("results_status", "")
        provider_review_required = state.get("provider_review_required", False)
        callback_timeframe = state.get("callback_timeframe", "24 to 48 hours")
        phone_last4 = self._phone_last4(state.get("phone_number", ""))

        return NodeConfig(
            name="results",
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Handle provider review or pending results. Never share results when provider_review_required is True. This step is important.

# Lab Order Information
- Test Type: {test_type}
- Ordering Physician: {ordering_physician}
- Results Status: {results_status}
- Provider Review Required: {provider_review_required}
- Callback Timeframe: {callback_timeframe}
- Phone on File (last 4): {phone_last4}

# Provider Review Required (provider_review_required=True)
Explain that the doctor needs to review, then ask about callback ONCE:
→ "{ordering_physician} needs to review these results. The doctor will call you within {callback_timeframe}. Is {phone_last4} still good to reach you?"

When caller confirms phone or gives new number → call confirm_callback immediately
When caller asks about results instead of answering → empathize briefly, then call confirm_callback(confirmed=true)
When caller asks "when will they call?" → answer "{callback_timeframe}" and call confirm_callback(confirmed=true)

# Results Pending
→ "Your {test_type} is still being processed. Would you like us to call when ready?"
When caller says yes → confirm phone number, then call confirm_callback
When caller asks "how long?" → answer with timeframe, wait for callback answer

# Examples

You: "The doctor will call you within {callback_timeframe}. Is {phone_last4} still good?"
Caller: "Is it bad news? Just tell me!"
→ "I understand how stressful this is. I can't share until the doctor reviews, but I'll make sure they call you."
→ call confirm_callback(confirmed=true)

Caller: "When will they call me?"
→ "Within {callback_timeframe}."
→ call confirm_callback(confirmed=true)

Caller: "Yes, that's my cell."
→ call confirm_callback(confirmed=true)

Caller: "Call my cell instead: 555-999-7777"
→ call confirm_callback(confirmed=true, new_number="5559997777")

# Guardrails
- NEVER share results when provider_review_required is True. This step is important.
- NEVER repeat information already said (callback timeframe, doctor will call, etc.)
- Ask about callback phone number only ONCE
- After anxious caller deflects TWICE: call confirm_callback(confirmed=true) - don't keep talking. This step is important.
- Only transfer if caller explicitly asks: "transfer me" or "speak to someone" """,
            }],
            functions=[
                FlowsFunctionSchema(
                    name="confirm_callback",
                    description="Confirm callback preference. Call after patient confirms/declines callback.",
                    properties={
                        "confirmed": {"type": "boolean", "description": "Whether patient confirmed/wants callback"},
                        "new_number": {"type": "string", "description": "New phone number if different (digits only), or empty"},
                    },
                    required=["confirmed"],
                    handler=self._confirm_callback_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

    def create_completion_node(self) -> NodeConfig:
        state = self.flow_manager.state
        test_type = state.get("test_type", "lab test")
        test_date = state.get("test_date", "")
        results_summary = state.get("results_summary", "")

        practice_info = self.patient_data.get("practice_info", {})
        office_hours = practice_info.get("office_hours", "Monday through Friday, 8 AM to 5 PM")
        facts = [(k, v) for k, v in [("Office hours", office_hours), ("Location", practice_info.get("location")), ("Parking", practice_info.get("parking"))] if v]
        practice_info_text = "\n".join(f"- {k}: {v}" for k, v in facts) if facts else "- Contact the front desk for practice information"

        results_info = f"# Lab Results Already Shared (for repeat requests)\n- Test: {test_type}\n- Date: {test_date}\n- Results: {results_summary}\n\n" if results_summary else ""

        return NodeConfig(
            name="completion",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
The lab results inquiry is complete. Thank the caller and check if they need anything else.

{results_info}# Questions You CAN Answer Directly
{practice_info_text}

# Scenario Handling

If patient asks to REPEAT the results:
→ Repeat the results in a SHORTER form (just the key findings)
→ Then ask "Is there anything else?"

If patient says GOODBYE / "that's all" / "bye" / "that's everything":
→ Say something warm and brief like "Take care!" or "You're welcome, take care!"
→ Call end_call IMMEDIATELY

NOTE: "Thank you" or "Great, thank you" alone is NOT a goodbye signal.
→ After "thank you", ask: "Is there anything else I can help with?"
→ Only end_call if they respond with clear goodbye like "No, that's all" or "Bye"

If patient asks a SIMPLE QUESTION (hours, location, parking):
→ Answer directly
→ Ask "Is there anything else I can help with?"

If patient needs SCHEDULING (book, cancel, reschedule appointment):
→ Call route_to_workflow with workflow="scheduling" IMMEDIATELY
→ Do NOT speak - the scheduling workflow will greet and offer slots

If patient needs PRESCRIPTION help (refill, medication status):
→ Call route_to_workflow with workflow="prescription_status" IMMEDIATELY
→ Do NOT speak - the function will share the prescription status directly

If patient needs BILLING or asks for a HUMAN:
→ Say "Let me connect you with someone who can help."
→ Call request_staff

If patient provides a DIFFERENT CALLBACK NUMBER (e.g., "call my cell at 555-1234"):
→ MUST call update_callback_number(new_number="5551234") - the function will respond with confirmation
→ Do NOT say "I've updated" without calling the function - the update won't happen unless you call it

# Example Flow
You: "Thanks for your patience with that. Is there anything else I can help you with today?"

Caller: "Actually yes, I need to schedule a follow-up appointment."
→ "I can help with that."
→ Call route_to_workflow with workflow="scheduling", reason="follow-up after lab results"

Caller: "What time do you close?"
→ "We're open {office_hours}."
→ "Anything else?"

Caller: "No, that's all. Thank you!"
→ "Take care!"
→ Call end_call

# Guardrails
- Keep responses brief and warm
- The caller's identity is already verified - no need to re-verify for scheduling or prescriptions
- Include relevant context in the reason field when routing (e.g., "follow-up after lab results")
- If caller is frustrated or asks for a human, call request_staff to transfer them
- NEVER add your own interpretation beyond what's in the results (e.g., "don't worry", "you're healthy", "this is good/bad")
- If caller expresses RELIEF or asks for reassurance (e.g., "so I'm okay?", "that's good right?", "so it's not cancer?"):
  → FIRST acknowledge their feelings warmly: "I can understand why you'd feel relieved" or "I know the wait must have been stressful"
  → THEN defer to doctor for full interpretation: "Your doctor can give you the complete picture of what this means for you."
  → Do NOT say "I can't explain" (you already shared the results - that would be contradictory)
  → Ask: "Is there anything else I can help with?"
- If caller expresses CONCERN or DISAPPOINTMENT (e.g., "still high", "was hoping for better"):
  → Acknowledge warmly: "I understand this isn't the news you were hoping for."
  → Say: "Your doctor can discuss what this means and next steps."
  → Ask: "Is there anything else I can help with?"
- Do NOT repeat the same information if already stated""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                self._request_staff_schema(),
                self._end_call_schema(),
                FlowsFunctionSchema(
                    name="update_callback_number",
                    description="Update callback phone number when caller provides a different one.",
                    properties={"new_number": {"type": "string", "description": "The new phone number (digits only or with common separators)"}},
                    required=["new_number"],
                    handler=self._update_callback_number_handler,
                ),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": "Is there anything else I can help you with today?"}],
        )

    # ==================== Node Creators: Utility/Bridge ====================

    def _create_post_workflow_node(self, target_flow, workflow_type: str, transition_message: str = "") -> NodeConfig:
        async def proceed_handler(args, flow_manager):
            if workflow_type == "prescription":
                return None, target_flow.create_status_node()
            return None, target_flow.create_scheduling_node()

        return NodeConfig(
            name=f"post_{workflow_type}",
            task_messages=[{"role": "system", "content": f"Call proceed_to_{workflow_type} immediately."}],
            functions=[FlowsFunctionSchema(
                name=f"proceed_to_{workflow_type}",
                description=f"Proceed to {workflow_type}.",
                properties={},
                required=[],
                handler=proceed_handler,
            )],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": transition_message}] if transition_message else None,
        )

    # ==================== Node Creators: Error/Edge Cases ====================

    def create_verification_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="verification_failed",
            task_messages=[{
                "role": "system",
                "content": """The information provided doesn't match our records.

Say: "I'm sorry, I wasn't able to verify your identity. Let me connect you with someone who can help."

Then listen to what caller says next:
- If caller mentions "schedule", "appointment", "book" → call route_to_workflow with workflow="scheduling"
- If caller mentions "prescription", "medication", "refill" → call route_to_workflow with workflow="prescription_status"
- If caller accepts the transfer, says "okay", or asks for a human → call request_staff

Do NOT say "transferring" or "please hold" - the transfer system handles that.""",
            }],
            functions=[self._route_to_workflow_schema(), self._request_staff_schema()],
            respond_immediately=True,
        )

    def create_transfer_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_failed",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """The transfer didn't go through. Apologize and offer alternatives. This step is important.

If caller wants to try the transfer again:
→ Call retry_transfer

If caller says goodbye or wants to end call:
→ Call end_call

If caller has a question you can answer:
→ Answer it, then ask if there's anything else""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="retry_transfer",
                    description="Retry the failed transfer when caller requests.",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                self._end_call_schema(),
            ],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": "I apologize, the transfer didn't go through."}],
        )

    # ==================== Handlers: Verification ====================

    async def _proceed_to_verification_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        logger.info("Flow: Proceeding to identity verification")
        return None, self.create_verification_node()

    async def _verify_identity_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        provided_name = args.get("name", "")
        provided_dob = args.get("date_of_birth", "")

        stored_name = flow_manager.state.get("patient_name", "")
        stored_dob = flow_manager.state.get("date_of_birth", "")

        provided_name_normalized = self._normalize_name(provided_name)
        stored_name_normalized = self._normalize_name(stored_name)
        provided_dob_normalized = self._normalize_dob(provided_dob)
        stored_dob_normalized = self._normalize_dob(stored_dob)

        name_match = provided_name_normalized == stored_name_normalized if stored_name_normalized else False
        dob_match = provided_dob_normalized == stored_dob_normalized if stored_dob_normalized else False

        logger.info(f"Flow: Identity verification - name_match={name_match}, dob_match={dob_match}")

        if name_match and dob_match:
            flow_manager.state["identity_verified"] = True

            if "," in provided_name:
                parts = [p.strip() for p in provided_name.split(",")]
                if len(parts) == 2:
                    flow_manager.state["last_name"] = parts[0]
                    flow_manager.state["first_name"] = parts[1]
            else:
                parts = provided_name.strip().split()
                if len(parts) >= 2:
                    flow_manager.state["first_name"] = parts[0]
                    flow_manager.state["last_name"] = " ".join(parts[1:])
                elif len(parts) == 1:
                    flow_manager.state["first_name"] = parts[0]

            flow_manager.state["patient_name"] = provided_name
            flow_manager.state["date_of_birth"] = stored_dob

            first_name = flow_manager.state.get("first_name", "there")

            patient_id = flow_manager.state.get("patient_id")
            await self._try_db_update(patient_id, "update_field", "identity_verified", True, error_msg="Error updating identity_verified")

            logger.info(f"Flow: Identity verified for {first_name} {flow_manager.state.get('last_name', '')}")

            results_status = flow_manager.state.get("results_status", "")
            provider_review_required = flow_manager.state.get("provider_review_required", False)
            results_summary = flow_manager.state.get("results_summary", "")
            test_type = flow_manager.state.get("test_type", "lab test")
            test_date = flow_manager.state.get("test_date", "")

            if results_status.lower() in ["ready", "available"] and not provider_review_required and results_summary:
                flow_manager.state["results_communicated"] = True
                await self._try_db_update(patient_id, "update_field", "results_communicated", True, error_msg="Error updating results_communicated")
                logger.info("Flow: Results communicated to patient (ready, no review required)")

                message = f"Thank you, {first_name}. I found your record. I can see you had a {test_type}"
                if test_date:
                    message += f" on {test_date}"
                message += f". Your results are in and show: {results_summary}"

                return message, self.create_completion_node()

            return f"Thank you, {first_name}. I found your record. Let me check the status of your lab results.", self.create_results_node()
        else:
            logger.warning(f"Flow: Identity verification failed - provided: {provided_name}, {provided_dob}")
            return None, self.create_verification_failed_node()

    # ==================== Handlers: Workflow Routing ====================

    async def _route_to_workflow_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")
        flow_manager.state["routed_to"] = f"{workflow} (AI)"

        if workflow not in self.WORKFLOW_FLOWS:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

        module_path, class_name = self.WORKFLOW_FLOWS[workflow]
        module = __import__(module_path, fromlist=[class_name])
        FlowClass = getattr(module, class_name)

        target_flow = FlowClass(
            patient_data=self.patient_data, flow_manager=flow_manager, main_llm=self.main_llm,
            context_aggregator=self.context_aggregator, transport=self.transport, pipeline=self.pipeline,
            organization_id=self.organization_id, cold_transfer_config=self.cold_transfer_config,
        )

        logger.info(f"Flow: Handing off to {class_name} with context: {reason}")

        if flow_manager.state.get("identity_verified"):
            first_name = flow_manager.state.get("first_name", "")
            if workflow == "scheduling":
                flow_manager.state["appointment_reason"] = reason
                flow_manager.state["appointment_type"] = "Returning Patient"
                msg = f"I can help with that, {first_name}!" if first_name else "I can help with that!"
                return None, self._create_post_workflow_node(target_flow, "scheduling", msg)
            elif workflow == "prescription_status":
                msg = f"Let me check on that for you, {first_name}." if first_name else "Let me check on that for you."
                return None, self._create_post_workflow_node(target_flow, "prescription", msg)

        return None, target_flow.create_handoff_entry_node(context=reason)

    # ==================== Handlers: Callbacks ====================

    async def _confirm_callback_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        confirmed = args.get("confirmed", False)
        new_number = args.get("new_number", "").strip()

        if confirmed:
            new_number_digits = await self._update_phone_number(new_number, flow_manager) if new_number else None
            logger.info("Flow: Callback confirmed")
            patient_id = flow_manager.state.get("patient_id")
            await self._try_db_update(patient_id, "update_patient", {"callback_confirmed": True}, error_msg="Error updating callback info")
            response = f"I've updated your callback number to the one ending in {self._phone_last4(new_number_digits)}." if new_number_digits else "I've confirmed your callback number."
        else:
            logger.info("Flow: Patient declined callback")
            response = "Understood. You can always call us back to check on your results."

        return response, self.create_completion_node()

    async def _update_callback_number_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        new_number = args.get("new_number", "").strip()
        if new_number:
            new_number_digits = await self._update_phone_number(new_number, flow_manager)
            return f"I've updated your callback number to the one ending in {self._phone_last4(new_number_digits)}. Is there anything else I can help with?", None
        return "I didn't catch the number. Could you repeat it?", None

    # ==================== Handlers: Transfers ====================

    async def _initiate_sip_transfer(self, flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        staff_number = self.cold_transfer_config.get("staff_number")
        if not staff_number:
            logger.warning("No staff transfer number configured")
            return None, self.create_transfer_failed_node()

        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"SIP transfer initiated: {staff_number}")

            patient_id = flow_manager.state.get("patient_id")
            await self._try_db_update(patient_id, "update_call_status", "Transferred", error_msg="Error updating call status")

            return None, NodeConfig(
                name="transfer_initiated",
                task_messages=[],
                functions=[],
                pre_actions=[{"type": "tts_say", "text": "Transferring you now, please hold."}],
                post_actions=[{"type": "end_conversation"}],
            )

        except Exception as e:
            logger.exception("SIP transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False
            return None, self.create_transfer_failed_node()

    async def _request_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        reason = args.get("reason", "caller requested transfer")
        flow_manager.state["transfer_reason"] = reason
        logger.info(f"Flow: Staff transfer requested - reason: {reason}")
        return await self._initiate_sip_transfer(flow_manager)

    async def _retry_transfer_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Retrying SIP transfer")
        return await self._initiate_sip_transfer(flow_manager)

    # ==================== Handlers: End Call ====================

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Ending call")
        patient_id = flow_manager.state.get("patient_id")
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

        return None, NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Thank the patient and say goodbye warmly."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )
