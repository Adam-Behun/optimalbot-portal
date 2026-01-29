import os
from typing import Any, Dict

from loguru import logger
from openai import AsyncOpenAI
from pipecat_flows import FlowManager, FlowsFunctionSchema, NodeConfig

from backend.models.patient import get_async_patient_db
from clients.demo_clinic_alpha.dialin_base_flow import DialinBaseFlow


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
        await client.chat.completions.create(model="gpt-4o-mini", messages=messages, max_tokens=1)
        logger.info("OpenAI cache warmed with lab_results prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class LabResultsFlow(DialinBaseFlow):
    WORKFLOW_FLOWS = {
        "scheduling": ("clients.demo_clinic_alpha.patient_scheduling.flow_definition", "PatientSchedulingFlow"),
        "prescription_status": ("clients.demo_clinic_alpha.prescription_status.flow_definition", "PrescriptionStatusFlow"),
    }

    def _init_domain_state(self):
        state = self.flow_manager.state
        for field in ["test_type", "test_date", "ordering_physician", "results_status", "results_summary"]:
            state[field] = self.call_data.get(field, "")
        state["provider_review_required"] = self.call_data.get("provider_review_required", False)
        state["callback_timeframe"] = self.call_data.get("callback_timeframe", "24 to 48 hours")
        state.setdefault("callback_confirmed", False)
        state.setdefault("results_communicated", False)

    def _get_workflow_type(self) -> str:
        return "lab_results"

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

    def _extract_lookup_record(self, patient: dict) -> dict:
        return {
            "patient_id": patient.get("patient_id"),
            "first_name": patient.get("first_name", ""),
            "last_name": patient.get("last_name", ""),
            "date_of_birth": patient.get("date_of_birth", ""),
            "phone_number": patient.get("phone_number", ""),
            "test_type": patient.get("test_type", ""),
            "test_date": patient.get("test_date", ""),
            "ordering_physician": patient.get("ordering_physician", ""),
            "results_status": patient.get("results_status", ""),
            "results_summary": patient.get("results_summary", ""),
            "provider_review_required": patient.get("provider_review_required", False),
            "callback_timeframe": patient.get("callback_timeframe", "24 to 48 hours"),
        }

    def _populate_domain_state(self, flow_manager: FlowManager, lookup: dict):
        flow_manager.state["test_type"] = lookup.get("test_type", "")
        flow_manager.state["test_date"] = lookup.get("test_date", "")
        flow_manager.state["ordering_physician"] = lookup.get("ordering_physician", "")
        flow_manager.state["results_status"] = lookup.get("results_status", "")
        flow_manager.state["results_summary"] = lookup.get("results_summary", "")
        flow_manager.state["provider_review_required"] = lookup.get("provider_review_required", False)
        flow_manager.state["callback_timeframe"] = lookup.get("callback_timeframe", "24 to 48 hours")

    def _route_after_verification(self, flow_manager: FlowManager) -> NodeConfig:
        return self._route_to_results_node(flow_manager)

    async def _update_phone_number(self, new_number: str, flow_manager: FlowManager) -> str:
        new_number_digits = self._normalize_phone(new_number)
        flow_manager.state["phone_number"] = new_number_digits
        logger.info(f"Flow: Callback number updated to {self._phone_last4(new_number_digits)}")
        patient_id = flow_manager.state.get("patient_id")
        await self._try_db_update(patient_id, "update_patient", {"caller_phone_number": new_number_digits}, error_msg="Error updating callback number")
        return new_number_digits

    async def _load_domain_data(self, patient_id: str) -> bool:
        if not patient_id:
            return False
        try:
            db = get_async_patient_db()
            patient = await db.find_patient_by_id(patient_id, self.organization_id)
            if not patient:
                logger.warning(f"Flow: Could not load domain data - patient {patient_id} not found")
                return False
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

    def get_initial_node(self) -> NodeConfig:
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

    def create_no_results_node(self) -> NodeConfig:
        return NodeConfig(
            name="no_results",
            task_messages=[{"role": "system", "content": "Call initiate_transfer immediately to connect caller with staff."}],
            functions=[
                FlowsFunctionSchema(
                    name="initiate_transfer",
                    description="Transfer to staff - no lab results on file.",
                    properties={},
                    required=[],
                    handler=self._initiate_transfer_handler,
                ),
            ],
            pre_actions=[{"type": "tts_say", "text": "I found your record, but I don't see any pending lab results. Let me connect you with a colleague who can help."}],
            respond_immediately=True,
        )

    def create_results_ready_node(self) -> NodeConfig:
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
            pre_actions=[{"type": "tts_say", "text": f"Thank you{self._greeting_name(first_name)}. Your {test_type} results are in. Would you like me to read them for you?"}],
            respond_immediately=False,
        )

    def create_results_pending_node(self) -> NodeConfig:
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
            pre_actions=[{"type": "tts_say", "text": f"Thank you{self._greeting_name(first_name)}. Your {test_type} is still being processed. Would you like us to call you when they're ready?"}],
            respond_immediately=False,
        )

    def create_provider_review_node(self) -> NodeConfig:
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
            pre_actions=[{"type": "tts_say", "text": f"Thank you{self._greeting_name(first_name)}. Your {test_type} results are in, but {ordering_physician} needs to review them before we can share the details. The doctor will call you within {callback_timeframe}. Is {phone_last4} still a good number to reach you?"}],
            respond_immediately=False,
        )

    def create_completion_node(self) -> NodeConfig:
        state = self.flow_manager.state
        results_communicated = state.get("results_communicated", False)
        practice_info = self.call_data.get("practice_info", {})
        office_hours = practice_info.get("office_hours", "Monday through Friday, 8 AM to 5 PM")
        facts = [(k, v) for k, v in [("Office hours", office_hours), ("Location", practice_info.get("location")), ("Parking", practice_info.get("parking"))] if v]
        practice_info_text = "\n".join(f"- {k}: {v}" for k, v in facts) if facts else "- Contact the front desk for practice information"
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
→ Call end_call IMMEDIATELY (do NOT say goodbye yourself - the function handles it)

NOTE: "Thank you" alone is NOT a goodbye signal - wait for their next response.
Only end_call if they give a clear goodbye like "No, that's all", "Bye", or "That's everything".

If patient asks a SIMPLE QUESTION (hours, location, parking):
→ Answer directly and wait for their response

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
- The caller is already verified
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
            pre_actions=self._completion_pre_actions(),
        )

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

    async def _proceed_to_lab_results_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        logger.info("Flow: Proceeding to lab results (phone lookup)")
        return None, self.create_patient_lookup_node()

    async def _proceed_to_other_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        logger.info("Flow: Routing to other_requests")
        return None, self.create_other_requests_node()

    async def _read_results_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        results_summary = flow_manager.state.get("results_summary", "")
        test_type = flow_manager.state.get("test_type", "lab test")
        patient_id = flow_manager.state.get("patient_id")
        if not results_summary:
            logger.warning("Flow: read_results called but no results_summary available")
            return "I don't have the results details available. Let me connect you with someone who can help.", self.create_transfer_failed_node()
        flow_manager.state["results_communicated"] = True
        await self._try_db_update(patient_id, "update_field", "results_communicated", True, error_msg="Error updating results_communicated")
        logger.info("Flow: Results read to patient via TTS")
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
        logger.info("Flow: Proceeding to completion")
        self._reset_anything_else_count()
        return None, self.create_completion_node()

    def _route_to_results_node(self, flow_manager: FlowManager) -> NodeConfig:
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
        return self.create_no_results_node()

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
        self._reset_anything_else_count()
        return response, self.create_completion_node()

    async def _update_callback_number_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        new_number = args.get("new_number", "").strip()
        if new_number:
            new_number_digits = await self._update_phone_number(new_number, flow_manager)
            self._reset_anything_else_count()
            return f"I've updated your callback number to the one ending in {self._phone_last4(new_number_digits)}.", self.create_completion_node()
        return "I didn't catch the number. Could you repeat it?", None
