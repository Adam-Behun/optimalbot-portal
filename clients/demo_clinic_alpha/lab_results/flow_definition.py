import os
from datetime import datetime, timezone
from typing import Dict, Any

from openai import AsyncOpenAI
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger

from backend.models import get_async_patient_db
from backend.sessions import get_async_session_db
from backend.utils import parse_natural_date
from handlers.transcript import save_transcript_to_db


class _MockFlowManager:
    def __init__(self):
        self.state = {}


async def warmup_openai(call_data: dict = None):
    try:
        call_data = call_data or {"organization_name": "Demo Clinic Alpha"}
        flow = LabResultsFlow(
            call_data=call_data,
            session_id="warmup",
            flow_manager=_MockFlowManager(),
            main_llm=None,
        )
        greeting_node = flow.create_greeting_node()

        messages = [{"role": m["role"], "content": m["content"]} for m in (greeting_node.get("role_messages") or []) + (greeting_node.get("task_messages") or [])]
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

    ALLOWS_NEW_PATIENTS = False

    WORKFLOW_FLOWS = {
        "scheduling": ("clients.demo_clinic_alpha.patient_scheduling.flow_definition", "PatientSchedulingFlow"),
        "prescription_status": ("clients.demo_clinic_alpha.prescription_status.flow_definition", "PrescriptionStatusFlow"),
    }

    # ==================== Initialization ====================

    def __init__(
        self,
        call_data: Dict[str, Any],
        session_id: str,
        flow_manager: FlowManager,
        main_llm=None,
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

        for field in ["patient_id", "patient_name", "first_name", "last_name",
                      "date_of_birth", "phone_number"]:
            default = None if field == "patient_id" else ""
            self.flow_manager.state[field] = (
                self.flow_manager.state.get(field) or self.call_data.get(field, default)
            )

        for field in ["test_type", "test_date", "ordering_physician", "results_status", "results_summary"]:
            self.flow_manager.state[field] = self.call_data.get(field, "")

        self.flow_manager.state["provider_review_required"] = self.call_data.get("provider_review_required", False)
        self.flow_manager.state["callback_timeframe"] = self.call_data.get("callback_timeframe", "24 to 48 hours")

        # Shared flags (unified across all flows)
        self.flow_manager.state.setdefault("identity_verified", False)
        self.flow_manager.state.setdefault("routed_to", "")
        self.flow_manager.state.setdefault("callback_confirmed", False)

        # Domain-specific flags
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

    async def _load_domain_data(self, patient_id: str) -> bool:
        """Load lab results domain data for a verified patient. Returns True if successful."""
        if not patient_id:
            return False
        try:
            db = get_async_patient_db()
            patient = await db.find_patient_by_id(patient_id, self.organization_id)
            if not patient:
                logger.warning(f"Flow: Could not load domain data - patient {patient_id} not found")
                return False

            # Load domain-specific data for lab results
            self.flow_manager.state["test_type"] = patient.get("test_type", "")
            self.flow_manager.state["test_date"] = patient.get("test_date", "")
            self.flow_manager.state["ordering_physician"] = patient.get("ordering_physician", "")
            self.flow_manager.state["results_status"] = patient.get("results_status", "")
            self.flow_manager.state["results_summary"] = patient.get("results_summary", "")
            self.flow_manager.state["provider_review_required"] = patient.get("provider_review_required", False)
            self.flow_manager.state["callback_timeframe"] = patient.get("callback_timeframe", "24 to 48 hours")
            logger.info(f"Flow: Loaded domain data for patient {patient_id}")
            return True
        except Exception as e:
            logger.error(f"Flow: Error loading domain data: {e}")
            return False

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
- Identity verification is handled by the flow's verification functions (lookup_by_phone, verify_dob, verify_identity)
- Do NOT ask verification questions directly - the flow will guide you to the right verification step
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
            description="End call. Use when caller says goodbye/bye/that's all. NOT just 'thank you'.",
            properties={},
            required=[],
            handler=self._end_call_handler,
        )

    def _request_staff_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="request_staff",
            description="Transfer to staff. ONLY use when caller explicitly asks for human/transfer, or for billing.",
            properties={"reason": {"type": "string", "description": "Brief reason (e.g., 'caller requested', 'billing question')"}},
            required=[],
            handler=self._request_staff_handler,
        )

    def _route_to_workflow_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="route_to_workflow",
            description="Route to another workflow. Use for scheduling or prescription requests.",
            properties={
                "workflow": {"type": "string", "enum": ["scheduling", "prescription_status"]},
                "reason": {"type": "string", "description": "Brief context (e.g., 'follow-up after lab results')"},
            },
            required=["workflow", "reason"],
            handler=self._route_to_workflow_handler,
        )

    # ==================== Node Creators: Entry Points ====================

    def get_initial_node(self) -> NodeConfig:
        """Entry point for dial-in calls. Returns the first node to execute."""
        return self.create_greeting_node()

    def create_greeting_node(self) -> NodeConfig:
        return NodeConfig(
            name="greeting",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """# Goal
This is the lab results line. Route the caller by calling the appropriate function.

# CRITICAL: Do NOT speak - just call a function
- Do NOT generate any speech or text response
- Do NOT ask verification questions
- Your ONLY job is to call the right function - the next node will handle the greeting

# Rules - CALL FUNCTION IMMEDIATELY
If caller mentions lab results, test results, blood work, labs, or checking on tests:
→ CALL proceed_to_lab_results (do NOT say anything)

For anything else (scheduling, prescriptions, billing, transfer, unclear):
→ CALL proceed_to_other (do NOT say anything)

IMPORTANT: Call a function immediately. Do NOT generate any speech.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_lab_results",
                    description="Caller wants lab/test results.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_lab_results_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_other",
                    description="Caller wants something other than lab results.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_other_handler,
                ),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": f"Hello, this is {self.organization_name} laboratory results. How can I help you?"}],
        )

    def create_other_requests_node(self) -> NodeConfig:
        return NodeConfig(
            name="other_requests",
            task_messages=[{
                "role": "system",
                "content": """# Goal
Caller reached the lab results line but needs something else. Route them.

# Rules
SCHEDULING (appointments, book, reschedule, cancel):
→ Call route_to_workflow with workflow="scheduling"

PRESCRIPTIONS (refill, medication, pharmacy):
→ Call route_to_workflow with workflow="prescription_status"

HUMAN REQUEST (explicitly asks for person/transfer) or BILLING:
→ Call request_staff

ACTUALLY WANTS LAB RESULTS (changed mind, mentions test/results):
→ Call proceed_to_lab_results

UNCLEAR:
→ Ask "Could you tell me more about what you need?" and stay on this node""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_lab_results",
                    description="Caller changed mind, wants lab results.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_lab_results_handler,
                ),
                self._route_to_workflow_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

    async def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        self.flow_manager.state["test_type"] = "biopsy" if "biopsy" in context.lower() else ""

        if self.flow_manager.state.get("identity_verified"):
            first_name = self.flow_manager.state.get("first_name", "")
            patient_id = self.flow_manager.state.get("patient_id")
            logger.info(f"Flow: Caller already verified as {first_name}, loading domain data")
            await self._load_domain_data(patient_id)
            return self._route_to_results_node(self.flow_manager)

        logger.info("Flow: Handoff entry - context stored, proceeding to phone lookup")
        return self.create_patient_lookup_node()

    # ==================== Node Creators: Phone Lookup Verification ====================

    def create_patient_lookup_node(self) -> NodeConfig:
        return NodeConfig(
            name="patient_lookup",
            task_messages=[{
                "role": "system",
                "content": """You just asked for the phone number. Wait for the caller to provide it.

# Phone Normalization
Spoken → Written (digits only):
- "five five five one two three four" → "5551234"
- "555-123-4567" → "5551234567"

# IMPORTANT: Confirm Before Lookup
After collecting the number, READ IT BACK to confirm before calling lookup_by_phone.
Format: "That's [number formatted as XXX-XXX-XXXX], correct?"

Example:
Caller: "five one six, five six six, seven one three two"
You: "That's 516-566-7132, correct?"
Caller: "Yes"
→ Call lookup_by_phone(phone_number="5165667132")

If caller says the number is wrong, ask them to repeat it.
If unclear, ask: "Could you repeat that number?"
If caller doesn't know their number, call request_staff.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="lookup_by_phone",
                    description="Look up patient by phone. Call after collecting phone number.",
                    properties={
                        "phone_number": {"type": "string", "description": "Digits only (e.g., '5551234567')"},
                    },
                    required=["phone_number"],
                    handler=self._lookup_by_phone_handler,
                ),
                self._request_staff_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": "Sounds good! What's the phone number on your account?"}],
            respond_immediately=False,
        )

    def create_verify_dob_node(self) -> NodeConfig:
        return NodeConfig(
            name="verify_dob",
            task_messages=[{
                "role": "system",
                "content": """Wait for the caller to provide their date of birth.

# Date Normalization
Spoken → Written:
- "march twenty second seventy eight" → "March 22, 1978"
- "three twenty two nineteen seventy eight" → "March 22, 1978"

Once you have DOB, call verify_dob immediately.

If unclear, ask: "Could you repeat that date?"
If caller can't verify, call request_staff.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="verify_dob",
                    description="Verify patient by DOB. Call after collecting date of birth.",
                    properties={
                        "date_of_birth": {"type": "string", "description": "Natural format (e.g., 'March 22, 1978')"},
                    },
                    required=["date_of_birth"],
                    handler=self._verify_dob_handler,
                ),
                self._request_staff_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": "Can you confirm your date of birth please?"}],
            post_actions=[{"type": "tts_say", "text": "Let me pull up your account."}],
            respond_immediately=False,
        )

    def create_patient_not_found_node(self) -> NodeConfig:
        """Patient not found on first attempt - allow retry."""
        state = self.flow_manager.state
        phone = state.get("_last_lookup_phone", "")
        dob = state.get("_last_lookup_dob", "")

        # Format phone for display
        phone_display = f"{phone[:3]}-{phone[3:6]}-{phone[6:]}" if len(phone) == 10 else phone

        return NodeConfig(
            name="patient_not_found",
            task_messages=[{
                "role": "system",
                "content": """The patient wasn't found. Allow them to retry with different info.

# Rules
- If caller provides new phone AND/OR date of birth → call retry_lookup with the new values
- If caller wants to speak to someone → call request_staff
- If caller says goodbye → call end_call

# Phone/DOB Normalization
Phone: digits only (e.g., "5551234567")
DOB: natural format (e.g., "March 22, 1978")""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="retry_lookup",
                    description="Retry lookup with corrected phone and/or DOB.",
                    properties={
                        "phone_number": {"type": "string", "description": "Corrected phone (digits only), or same if unchanged"},
                        "date_of_birth": {"type": "string", "description": "Corrected DOB (natural format), or same if unchanged"},
                    },
                    required=["phone_number", "date_of_birth"],
                    handler=self._retry_lookup_handler,
                ),
                self._request_staff_schema(),
                self._end_call_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": f"I'm sorry, I couldn't find a record for {phone_display} with that date of birth. Could you double-check those for me?"}],
            respond_immediately=False,
        )

    def create_patient_not_found_final_node(self) -> NodeConfig:
        """Patient not found after retry - auto-transfer to staff."""
        return NodeConfig(
            name="patient_not_found_final",
            task_messages=[{"role": "system", "content": "Call initiate_transfer immediately to connect caller with staff."}],
            functions=[
                FlowsFunctionSchema(
                    name="initiate_transfer",
                    description="Transfer to staff after patient not found.",
                    properties={},
                    required=[],
                    handler=self._initiate_transfer_after_message_handler,
                ),
            ],
            pre_actions=[{"type": "tts_say", "text": "I still couldn't find your record. Let me connect you with a colleague who can help."}],
            respond_immediately=True,
        )

    def create_no_results_node(self) -> NodeConfig:
        """Node for when patient is found but has no lab results on file. Auto-transfers to staff."""
        return NodeConfig(
            name="no_results",
            task_messages=[{"role": "system", "content": "Call initiate_transfer immediately to connect caller with staff."}],
            functions=[
                FlowsFunctionSchema(
                    name="initiate_transfer",
                    description="Transfer to staff - no lab results on file.",
                    properties={},
                    required=[],
                    handler=self._initiate_transfer_after_message_handler,
                ),
            ],
            pre_actions=[{"type": "tts_say", "text": "I found your record, but I don't see any pending lab results. Let me connect you with a colleague who can help."}],
            respond_immediately=True,
        )

    # ==================== Node Creators: Results Nodes ====================

    def create_results_ready_node(self) -> NodeConfig:
        """Results are ready and can be shared. Ask if patient wants to hear them."""
        state = self.flow_manager.state
        first_name = state.get("first_name", "")
        test_type = state.get("test_type", "lab test")

        return NodeConfig(
            name="results_ready",
            task_messages=[{
                "role": "system",
                "content": """The patient's lab results are ready. Wait for their response.

# Rules
- If patient says YES (wants to hear results) → call read_results
- If patient says NO (doesn't want to hear now) → call proceed_to_completion
- If patient asks for a human → call request_staff

Do NOT read the results yourself. The read_results function will handle that.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="read_results",
                    description="Read the lab results to the patient. Call when patient wants to hear results.",
                    properties={},
                    required=[],
                    handler=self._read_results_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_completion",
                    description="Skip reading results and go to completion. Call when patient declines.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_completion_handler,
                ),
                self._request_staff_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": f"Thank you, {first_name}. Your {test_type} results are in. Would you like me to read them for you?"}],
            respond_immediately=False,
        )

    def create_results_pending_node(self) -> NodeConfig:
        """Results are still being processed."""
        state = self.flow_manager.state
        first_name = state.get("first_name", "")
        test_type = state.get("test_type", "lab test")

        return NodeConfig(
            name="results_pending",
            task_messages=[{
                "role": "system",
                "content": """Results are still being processed. Ask about callback.

# Rules
After asking if they want a callback:
- Patient says yes → call confirm_callback(confirmed=true)
- Patient gives new number → call confirm_callback(confirmed=true, new_number="5551234567")
- Patient declines callback → call confirm_callback(confirmed=false)
- Patient asks for human → call request_staff

# Phone Normalization
Digits only: "555-123-4567" → "5551234567" """,
            }],
            functions=[
                FlowsFunctionSchema(
                    name="confirm_callback",
                    description="Confirm callback preference. Call after patient responds.",
                    properties={
                        "confirmed": {"type": "boolean", "description": "True unless patient explicitly declines"},
                        "new_number": {"type": "string", "description": "New number digits only, or empty to keep current"},
                    },
                    required=["confirmed"],
                    handler=self._confirm_callback_handler,
                ),
                self._request_staff_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": f"Thank you, {first_name}. Your {test_type} is still being processed. Would you like us to call you when they're ready?"}],
            respond_immediately=False,
        )

    def create_provider_review_node(self) -> NodeConfig:
        """Results require provider review before sharing."""
        state = self.flow_manager.state
        first_name = state.get("first_name", "")
        test_type = state.get("test_type", "lab test")
        ordering_physician = state.get("ordering_physician", "your doctor")
        callback_timeframe = state.get("callback_timeframe", "24 to 48 hours")
        phone_last4 = self._phone_last4(state.get("phone_number", ""))

        return NodeConfig(
            name="provider_review",
            task_messages=[{
                "role": "system",
                "content": f"""Results require doctor review. NEVER share the actual results.

# CRITICAL
- NEVER share results when provider_review_required is True
- Only explain the doctor will call

# After the pre_action message, handle caller response:
- Caller confirms phone → call confirm_callback(confirmed=true)
- Caller gives new number → call confirm_callback(confirmed=true, new_number="...")
- Caller is anxious/asks about results → empathize briefly: "I understand how stressful this is. The doctor will call you as soon as they've reviewed everything." Then call confirm_callback(confirmed=true)
- Caller asks "when will they call?" → "{callback_timeframe}" then call confirm_callback(confirmed=true)
- Caller asks for human → call request_staff

# Phone Normalization
Digits only: "555-123-4567" → "5551234567" """,
            }],
            functions=[
                FlowsFunctionSchema(
                    name="confirm_callback",
                    description="Confirm callback. Call after ANY response to callback question.",
                    properties={
                        "confirmed": {"type": "boolean", "description": "True unless patient explicitly declines"},
                        "new_number": {"type": "string", "description": "New number digits only, or empty to keep current"},
                    },
                    required=["confirmed"],
                    handler=self._confirm_callback_handler,
                ),
                self._request_staff_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": f"Thank you, {first_name}. Your {test_type} results are in, but {ordering_physician} needs to review them before we can share the details. The doctor will call you within {callback_timeframe}. Is {phone_last4} still a good number to reach you?"}],
            respond_immediately=False,
        )

    def create_completion_node(self) -> NodeConfig:
        state = self.flow_manager.state
        results_communicated = state.get("results_communicated", False)

        practice_info = self.call_data.get("practice_info", {})
        office_hours = practice_info.get("office_hours", "Monday through Friday, 8 AM to 5 PM")
        facts = [(k, v) for k, v in [("Office hours", office_hours), ("Location", practice_info.get("location")), ("Parking", practice_info.get("parking"))] if v]
        practice_info_text = "\n".join(f"- {k}: {v}" for k, v in facts) if facts else "- Contact the front desk for practice information"

        # Only show read_results option if results were already shared
        results_note = """
If patient asks to REPEAT the results:
→ Call read_results to read them again
→ Do NOT read the results yourself - the function handles it""" if results_communicated else ""

        return NodeConfig(
            name="completion",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Check if the caller needs anything else.

# Questions You CAN Answer Directly
{practice_info_text}

# Scenario Handling
{results_note}
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
→ Call route_to_workflow with workflow="scheduling"

If patient needs PRESCRIPTION help (refill, medication status):
→ Call route_to_workflow with workflow="prescription_status"

If patient needs BILLING or asks for a HUMAN:
→ Say "Let me connect you with someone who can help."
→ Call request_staff

If patient provides a DIFFERENT CALLBACK NUMBER:
→ Call update_callback_number with the new number

# Guardrails
- Keep responses brief and warm
- The caller's identity is already verified
- If caller is frustrated or asks for a human, call request_staff
- NEVER interpret results (e.g., "don't worry", "you're healthy", "this is good/bad")
- If caller expresses RELIEF → acknowledge warmly, defer to doctor for interpretation
- If caller expresses CONCERN → acknowledge warmly, suggest doctor can discuss next steps""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                self._request_staff_schema(),
                self._end_call_schema(),
                FlowsFunctionSchema(
                    name="update_callback_number",
                    description="Update callback number. Call when caller provides a different phone number.",
                    properties={"new_number": {"type": "string", "description": "Digits only (e.g., '5551234567')"}},
                    required=["new_number"],
                    handler=self._update_callback_number_handler,
                ),
            ] + ([FlowsFunctionSchema(
                name="read_results",
                description="Read the lab results again. Call when patient asks to repeat results.",
                properties={},
                required=[],
                handler=self._read_results_handler,
            )] if results_communicated else []),
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

    def create_transfer_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_failed",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """The transfer didn't go through. Wait for caller's response.

If caller wants to try again / says yes:
→ Call retry_transfer

If caller wants to schedule an appointment:
→ Call route_to_workflow with workflow="scheduling"

If caller wants to check prescriptions:
→ Call route_to_workflow with workflow="prescription_status"

If caller says goodbye or wants to end call:
→ Call end_call""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="retry_transfer",
                    description="Retry the failed transfer.",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                self._route_to_workflow_schema(),
                self._end_call_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": "I apologize, the transfer didn't go through. Let me try again."}],
        )

    # ==================== Handlers: Phone Lookup Verification ====================

    async def _lookup_by_phone_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        phone_digits = self._normalize_phone(args.get("phone_number", ""))
        logger.info(f"Flow: Looking up phone: {self._phone_last4(phone_digits)}")

        # Store for patient_not_found node display
        flow_manager.state["_last_lookup_phone"] = phone_digits

        if patient := await get_async_patient_db().find_patient_by_phone(phone_digits, self.organization_id, "lab_results"):
            # Check if patient has DOB for verification
            stored_dob = patient.get("date_of_birth", "")
            if not stored_dob:
                # No DOB on file - can't verify, transfer to staff
                logger.warning("Flow: Patient found but no DOB on file - transferring to staff")
                return None, self.create_patient_not_found_final_node()

            # Store lookup record for DOB verification
            flow_manager.state["_lookup_record"] = {
                "patient_id": patient.get("patient_id"),
                "first_name": patient.get("first_name", ""),
                "last_name": patient.get("last_name", ""),
                "date_of_birth": stored_dob,
                "phone_number": patient.get("phone_number", ""),
                "test_type": patient.get("test_type", ""),
                "test_date": patient.get("test_date", ""),
                "ordering_physician": patient.get("ordering_physician", ""),
                "results_status": patient.get("results_status", ""),
                "results_summary": patient.get("results_summary", ""),
                "provider_review_required": patient.get("provider_review_required", False),
                "callback_timeframe": patient.get("callback_timeframe", "24 to 48 hours"),
            }
            logger.info("Flow: Found record, requesting DOB")
            return None, self.create_verify_dob_node()

        # Not found - allow retry
        logger.info("Flow: No patient found - routing to patient_not_found for retry")
        flow_manager.state["_last_lookup_dob"] = ""
        return None, self.create_patient_not_found_node()

    async def _verify_dob_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        provided = parse_natural_date(args.get("date_of_birth", "").strip())
        lookup = flow_manager.state.get("_lookup_record", {})
        stored = lookup.get("date_of_birth", "")
        logger.info(f"Flow: Verifying DOB - provided: {provided}, stored: {stored}")

        # Store for patient_not_found display
        flow_manager.state["_last_lookup_dob"] = provided or args.get("date_of_birth", "").strip()

        if provided and provided == stored:
            # DOB matches - mark verified and load domain data
            flow_manager.state["identity_verified"] = True
            flow_manager.state["patient_id"] = lookup.get("patient_id")
            flow_manager.state["first_name"] = lookup.get("first_name", "")
            flow_manager.state["last_name"] = lookup.get("last_name", "")
            flow_manager.state["date_of_birth"] = stored
            flow_manager.state["phone_number"] = lookup.get("phone_number", "")
            flow_manager.state["patient_name"] = f"{lookup.get('first_name', '')} {lookup.get('last_name', '')}".strip()

            # Load domain-specific data for lab results
            flow_manager.state["test_type"] = lookup.get("test_type", "")
            flow_manager.state["test_date"] = lookup.get("test_date", "")
            flow_manager.state["ordering_physician"] = lookup.get("ordering_physician", "")
            flow_manager.state["results_status"] = lookup.get("results_status", "")
            flow_manager.state["results_summary"] = lookup.get("results_summary", "")
            flow_manager.state["provider_review_required"] = lookup.get("provider_review_required", False)
            flow_manager.state["callback_timeframe"] = lookup.get("callback_timeframe", "24 to 48 hours")

            flow_manager.state.pop("_lookup_record", None)
            first_name = lookup.get("first_name", "")
            patient_id = flow_manager.state.get("patient_id")
            logger.info(f"Flow: DOB verified for {first_name}")

            # Update DB with verification
            await self._try_db_update(patient_id, "update_field", "identity_verified", True, error_msg="Error updating identity_verified")

            # Route to appropriate results node
            return None, self._route_to_results_node(flow_manager)

        # DOB mismatch - allow retry
        logger.warning("Flow: DOB mismatch - routing to patient_not_found for retry")
        flow_manager.state.pop("_lookup_record", None)
        return "That doesn't match our records.", self.create_patient_not_found_node()

    # ==================== Handlers: Greeting ====================

    async def _proceed_to_lab_results_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        """Proceed to lab results flow - phone lookup."""
        logger.info("Flow: Proceeding to lab results (phone lookup)")
        return None, self.create_patient_lookup_node()

    async def _proceed_to_other_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        """Route to other_requests node for non-lab-results requests."""
        logger.info("Flow: Routing to other_requests")
        return None, self.create_other_requests_node()

    # ==================== Handlers: Results ====================

    async def _read_results_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        """Read lab results via scripted TTS. No LLM interpretation."""
        results_summary = flow_manager.state.get("results_summary", "")
        test_type = flow_manager.state.get("test_type", "lab test")
        patient_id = flow_manager.state.get("patient_id")

        if not results_summary:
            logger.warning("Flow: read_results called but no results_summary available")
            return "I don't have the results details available. Let me connect you with someone who can help.", self.create_transfer_failed_node()

        # Mark results as communicated
        flow_manager.state["results_communicated"] = True
        await self._try_db_update(patient_id, "update_field", "results_communicated", True, error_msg="Error updating results_communicated")
        logger.info("Flow: Results read to patient via TTS")

        # Return the results as TTS via the completion node's pre_action
        results_text = f"Your {test_type} results show: {results_summary}."
        return None, NodeConfig(
            name="results_read",
            task_messages=[{"role": "system", "content": "Call proceed_to_completion immediately after results are read."}],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_completion",
                    description="Continue to completion after reading results.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_completion_handler,
                ),
            ],
            pre_actions=[{"type": "tts_say", "text": results_text}],
            respond_immediately=True,
        )

    async def _proceed_to_completion_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        """Skip reading results and go to completion."""
        logger.info("Flow: Proceeding to completion")
        return None, self.create_completion_node()

    async def _retry_lookup_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        """Retry patient lookup with corrected phone and/or DOB."""
        phone_digits = self._normalize_phone(args.get("phone_number", ""))
        provided_dob = args.get("date_of_birth", "").strip()
        normalized_dob = parse_natural_date(provided_dob) if provided_dob else None

        logger.info(f"Flow: Retry lookup - phone: {self._phone_last4(phone_digits)}, dob: {normalized_dob}")

        # Store for display in patient_not_found_final if needed
        flow_manager.state["_last_lookup_phone"] = phone_digits
        flow_manager.state["_last_lookup_dob"] = normalized_dob or provided_dob

        # Look up patient
        patient = await get_async_patient_db().find_patient_by_phone(phone_digits, self.organization_id, "lab_results")

        if patient:
            stored_dob = patient.get("date_of_birth", "")

            # Check DOB match
            if normalized_dob and normalized_dob == stored_dob:
                # Success! Store patient data and route to results
                flow_manager.state["identity_verified"] = True
                flow_manager.state["patient_id"] = patient.get("patient_id")
                flow_manager.state["first_name"] = patient.get("first_name", "")
                flow_manager.state["last_name"] = patient.get("last_name", "")
                flow_manager.state["date_of_birth"] = stored_dob
                flow_manager.state["phone_number"] = patient.get("phone_number", "")
                flow_manager.state["patient_name"] = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()

                # Load domain data
                flow_manager.state["test_type"] = patient.get("test_type", "")
                flow_manager.state["test_date"] = patient.get("test_date", "")
                flow_manager.state["ordering_physician"] = patient.get("ordering_physician", "")
                flow_manager.state["results_status"] = patient.get("results_status", "")
                flow_manager.state["results_summary"] = patient.get("results_summary", "")
                flow_manager.state["provider_review_required"] = patient.get("provider_review_required", False)
                flow_manager.state["callback_timeframe"] = patient.get("callback_timeframe", "24 to 48 hours")

                patient_id = flow_manager.state.get("patient_id")
                await self._try_db_update(patient_id, "update_field", "identity_verified", True, error_msg="Error updating identity_verified")

                logger.info(f"Flow: Retry successful - patient verified")
                return None, self._route_to_results_node(flow_manager)

        # Still not found - route to final transfer
        logger.info("Flow: Retry failed - routing to patient_not_found_final")
        return None, self.create_patient_not_found_final_node()

    def _route_to_results_node(self, flow_manager: FlowManager) -> NodeConfig:
        """Route to appropriate results node based on status."""
        results_status = flow_manager.state.get("results_status", "")
        provider_review_required = flow_manager.state.get("provider_review_required", False)
        results_summary = flow_manager.state.get("results_summary", "")

        if not results_status:
            return self.create_no_results_node()

        if provider_review_required:
            return self.create_provider_review_node()

        if results_status.lower() in ["pending", "processing"]:
            return self.create_results_pending_node()

        if results_status.lower() in ["ready", "available"] and results_summary:
            return self.create_results_ready_node()

        # Fallback - no results
        return self.create_no_results_node()

    # ==================== Handlers: Transfer ====================

    async def _initiate_transfer_after_message_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        """Initiate SIP transfer after the apology message has played."""
        logger.info("Flow: Initiating transfer after message")
        return await self._initiate_sip_transfer(flow_manager)

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
            call_data=self.call_data, session_id=self.session_id, flow_manager=flow_manager,
            main_llm=self.main_llm, context_aggregator=self.context_aggregator,
            transport=self.transport, pipeline=self.pipeline,
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

        return None, await target_flow.create_handoff_entry_node(context=reason)

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
        logger.info(f"Flow: Staff transfer requested - reason: {reason}")
        return await self._initiate_sip_transfer(flow_manager)

    async def _retry_transfer_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Retrying SIP transfer")
        return await self._initiate_sip_transfer(flow_manager)

    # ==================== Handlers: End Call ====================

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Ending call")
        patient_id = flow_manager.state.get("patient_id")
        session_db = get_async_session_db()

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
                await patient_db.update_patient(patient_id, {
                    "call_status": "Completed",
                    "last_call_session_id": self.session_id,
                }, self.organization_id)

        except Exception as e:
            logger.exception("Error in end_call_handler")
            try:
                await session_db.update_session(self.session_id, {"status": "failed"}, self.organization_id)
            except Exception as db_error:
                logger.error(f"Failed to update session status: {db_error}")

        return None, NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Thank the patient and say goodbye warmly."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )
