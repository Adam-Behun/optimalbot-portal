import importlib
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
from .schema import MEDICATIONS, PRESCRIPTION_STATUS


class _MockFlowManager:
    def __init__(self):
        self.state = {}


async def warmup_openai(call_data: dict = None):
    try:
        call_data = call_data or {"organization_name": "Demo Clinic Alpha"}
        flow = PrescriptionStatusFlow(
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
        messages.append({"role": "user", "content": "Hi, I'm calling about my prescription"})

        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=1,
        )
        logger.info("OpenAI cache warmed with prescription_status prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class PrescriptionStatusFlow:

    # ==================== Class Constants ====================

    ALLOWS_NEW_PATIENTS = False

    WORKFLOW_FLOWS = {
        "lab_results": ("clients.demo_clinic_alpha.lab_results.flow_definition", "LabResultsFlow", "create_results_node"),
        "scheduling": ("clients.demo_clinic_alpha.patient_scheduling.flow_definition", "PatientSchedulingFlow", "create_scheduling_node"),
    }

    IDENTITY_FIELDS = ["patient_id", "patient_name", "first_name", "last_name", "date_of_birth", "phone_number"]
    RX_FIELDS = ["medication_name", "dosage", "prescribing_physician", "refill_status", "last_filled_date", "next_refill_date",
                 "pharmacy_name", "pharmacy_phone", "pharmacy_address"]

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

        state = self.flow_manager.state
        for field in self.IDENTITY_FIELDS:
            state[field] = state.get(field) or self.call_data.get(field, "" if field != "patient_id" else None)
        for field in self.RX_FIELDS:
            state[field] = self.call_data.get(field, "")
        state["refills_remaining"] = self.call_data.get("refills_remaining", 0)
        state["prescriptions"] = self.call_data.get("prescriptions", [])

        # Shared flags (unified across all flows)
        state.setdefault("identity_verified", False)
        state.setdefault("routed_to", "")
        state.setdefault("callback_confirmed", False)

        # Retry counters (max 2 for each)
        state.setdefault("lookup_attempts", 0)
        state.setdefault("medication_select_attempts", 0)
        state.setdefault("transfer_attempts", 0)

        # Prescription flow state
        state.setdefault("mentioned_medication", None)
        state.setdefault("selected_prescription", None)

    # ==================== Helpers: Database ====================

    async def _try_db_update(self, patient_id: str, method: str, *args, error_msg: str = "DB update error"):
        if not patient_id:
            return
        try:
            db = get_async_patient_db()
            await getattr(db, method)(patient_id, *args, self.organization_id)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")

    async def _load_domain_data(self, patient_id: str) -> bool:
        """Load prescription domain data for a verified patient. Returns True if successful."""
        if not patient_id:
            return False
        try:
            db = get_async_patient_db()
            patient = await db.find_patient_by_id(patient_id, self.organization_id)
            if not patient:
                logger.warning(f"Flow: Could not load domain data - patient {patient_id} not found")
                return False

            # Load domain-specific data for prescriptions
            self.flow_manager.state["medication_name"] = patient.get("medication_name", "")
            self.flow_manager.state["dosage"] = patient.get("dosage", "")
            self.flow_manager.state["prescribing_physician"] = patient.get("prescribing_physician", "")
            self.flow_manager.state["refill_status"] = patient.get("refill_status", "")
            self.flow_manager.state["refills_remaining"] = patient.get("refills_remaining", 0)
            self.flow_manager.state["last_filled_date"] = patient.get("last_filled_date", "")
            self.flow_manager.state["next_refill_date"] = patient.get("next_refill_date", "")
            self.flow_manager.state["pharmacy_name"] = patient.get("pharmacy_name", "")
            self.flow_manager.state["pharmacy_phone"] = patient.get("pharmacy_phone", "")
            self.flow_manager.state["pharmacy_address"] = patient.get("pharmacy_address", "")
            self.flow_manager.state["prescriptions"] = patient.get("prescriptions", [])
            logger.info(f"Flow: Loaded domain data for patient {patient_id}")
            return True
        except Exception as e:
            logger.error(f"Flow: Error loading domain data: {e}")
            return False

    # ==================== Helpers: Normalization ====================

    def _get_full_name(self) -> str:
        first = self.flow_manager.state.get("first_name", "")
        last = self.flow_manager.state.get("last_name", "")
        if first and last:
            return f"{first} {last}"
        return first or last or ""

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

    # ==================== Helpers: Medication Matching ====================

    def _find_medication_match(self, mentioned: str, prescriptions: list) -> dict | None:
        """Match mentioned medication against prescriptions using GLP-1 aliases."""
        if not mentioned:
            return None
        mentioned_lower = mentioned.lower().strip()

        for rx in prescriptions:
            rx_name = rx.get("medication_name", "").lower()

            # Direct match
            if mentioned_lower in rx_name or rx_name in mentioned_lower:
                return rx

            # Check against MEDICATIONS aliases from schema
            for brand_name, med_info in MEDICATIONS.items():
                if rx_name in brand_name.lower() or brand_name.lower() in rx_name:
                    aliases = [a.lower() for a in med_info.get("aliases", [])]
                    if mentioned_lower in aliases or any(a in mentioned_lower for a in aliases):
                        return rx
                    # Also check generic name
                    generic = med_info.get("generic", "").lower()
                    if generic and (mentioned_lower == generic or generic in mentioned_lower):
                        return rx

        return None

    def _get_status_key(self, prescription: dict) -> str:
        """Determine the status key for routing to appropriate status node."""
        status = prescription.get("status", prescription.get("refill_status", "")).lower()
        refills = prescription.get("refills_remaining", 0)

        # Map various status strings to status node keys
        if status in ("sent", "sent to pharmacy"):
            return "status_sent"
        elif status in ("pending", "pending prior auth", "pending doctor approval", "awaiting prior auth"):
            return "status_pending"
        elif status in ("ready", "ready for pickup"):
            return "status_ready"
        elif status in ("too early", "too early to refill"):
            return "status_too_early"
        elif status in ("renewal", "needs renewal", "expired"):
            return "status_renewal"
        elif refills > 0 or status in ("active", "refills", "refills available"):
            return "status_refills"
        else:
            # Default to pending if unclear
            return "status_pending"

    def _format_status_message(self, status_key: str) -> str:
        """Format status message using template from schema."""
        state = self.flow_manager.state
        template = PRESCRIPTION_STATUS.get(status_key, {}).get("template", "")

        return template.format(
            medication_name=state.get("medication_name", "your medication"),
            dosage=state.get("dosage", ""),
            pharmacy_name=state.get("pharmacy_name", "your pharmacy"),
            pharmacy_phone=state.get("pharmacy_phone", ""),
            next_refill_date=state.get("next_refill_date", ""),
            refills_remaining=state.get("refills_remaining", 0),
            prescribing_physician=state.get("prescribing_physician", "your doctor"),
        )

    # ==================== Helpers: Prompts ====================

    def _get_global_instructions(self) -> str:
        return f"""You are Jamie, a virtual assistant for {self.organization_name}, answering inbound calls from patients about their prescription refills.

# Voice Conversation Style
You are on a phone call with a patient. Your responses will be converted to speech:
- Speak naturally and warmly, like a helpful clinic staff member
- Keep responses concise and clear—one or two sentences is usually enough
- Use natural acknowledgments: "Of course", "I understand", "Let me check that for you"
- NEVER use bullet points, numbered lists, asterisks, bold, or any markdown formatting
- Say "Got it" or "One moment" instead of robotic phrases

# Handling Speech Recognition
Input is transcribed from speech and may contain errors:
- Silently correct obvious transcription mistakes based on context
- "for too ate" likely means "4 2 8" in a phone number context
- If truly unclear, ask them to repeat naturally: "Sorry, I didn't catch that"

# HIPAA Compliance
- You MUST verify the patient before discussing any prescription information
- Verification is handled by the flow functions - do NOT ask for name/DOB directly
- If verification fails, do not provide any prescription details

# Guardrails
- Never provide medical advice about medications
- If prescription needs doctor approval, explain they will be contacted
- If you don't have information, say so honestly
- Stay on topic: prescription status inquiries only
- If caller is frustrated or asks for a human, offer to transfer them"""

    # ==================== Helpers: Function Schemas ====================

    def _end_call_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="end_call",
            description="End call when caller says goodbye or confirms done.",
            properties={},
            required=[],
            handler=self._end_call_handler,
        )

    def _request_staff_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="request_staff",
            description="Transfer to human staff. Use for: third-party callers, pharmacy changes, expedite requests, medical concerns, or explicit human requests.",
            properties={
                "patient_confirmed": {"type": "boolean", "description": "True if patient explicitly asked for human"},
                "reason": {"type": "string", "description": "Brief reason: third_party, pharmacy_change, expedite, medical_question"},
            },
            required=[],
            handler=self._request_staff_handler,
        )

    def _route_to_workflow_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="route_to_workflow",
            description="Route to AI workflow. scheduling=appointments, lab_results=test results. Caller already verified.",
            properties={
                "workflow": {"type": "string", "enum": ["lab_results", "scheduling"], "description": "Target workflow"},
                "reason": {"type": "string", "description": "Brief context for next workflow"},
            },
            required=["workflow", "reason"],
            handler=self._route_to_workflow_handler,
        )

    def _check_another_medication_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="check_another_medication",
            description="Patient asks about another medication/prescription/refill.",
            properties={},
            required=[],
            handler=self._check_another_medication_handler,
        )

    # ==================== Node Creators: Entry Points ====================

    def get_initial_node(self) -> NodeConfig:
        """Entry point for dial-in calls. Returns the first node to execute."""
        return self.create_greeting_node()

    def create_greeting_node(self) -> NodeConfig:
        greeting_text = f"Thank you for calling {self.organization_name}. This is Jamie. How can I help you today?"

        return NodeConfig(
            name="greeting",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """# Goal
Route the caller by calling the appropriate function.

# CRITICAL: Do NOT speak - just call a function
- Do NOT generate any speech or text response
- Do NOT ask for medication names or any other information
- Your ONLY job is to call the right function - the next node will handle everything

# Rules - CALL FUNCTION IMMEDIATELY
If caller mentions prescription, refill, medication, or Rx:
→ CALL proceed_to_prescription_status (do NOT say anything)
→ Include mentioned_medication ONLY if caller already named a specific medication

If caller mentions scheduling, appointment, lab results, or blood work:
→ CALL proceed_to_other (do NOT say anything)

If caller mentions billing, insurance, or asks for a human:
→ CALL request_staff (do NOT say anything)

# Examples
Caller: "I'm calling about my Ozempic prescription."
→ Call proceed_to_prescription_status with mentioned_medication="Ozempic"

Caller: "I need to check on a refill."
→ Call proceed_to_prescription_status (NO medication param - do NOT ask)

Caller: "I'd like to check on my prescription stuff."
→ Call proceed_to_prescription_status (NO medication param - do NOT ask)

# Unrecognized Intent
ONLY if truly unclear, ask: "I can help with prescriptions, appointments, or lab results. What are you calling about?"

IMPORTANT: Call a function immediately. Do NOT generate any speech.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_prescription_status",
                    description="Caller asks about prescription/refill/medication. Include mentioned_medication if caller named a specific medication.",
                    properties={
                        "mentioned_medication": {"type": "string", "description": "Medication name if caller mentioned one (e.g., 'Ozempic', 'Wegovy')"},
                    },
                    required=[],
                    handler=self._proceed_to_prescription_status_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_other",
                    description="Caller needs scheduling, lab results, or other non-prescription services.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_other_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": greeting_text}],
        )

    async def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        context_lower = context.lower()
        if "lisinopril" in context_lower:
            self.flow_manager.state["medication_name"] = "lisinopril"
        if "prior auth" in context_lower:
            self.flow_manager.state["issue_type"] = "prior_authorization"

        if self.flow_manager.state.get("identity_verified"):
            first_name = self.flow_manager.state.get("first_name", "")
            patient_id = self.flow_manager.state.get("patient_id")
            logger.info(f"Flow: Caller already verified as {first_name}, loading domain data")
            await self._load_domain_data(patient_id)
            # Route to appropriate status node
            prescriptions = self.flow_manager.state.get("prescriptions", [])
            if len(prescriptions) == 1:
                return self._route_to_status_node(prescriptions[0])
            elif len(prescriptions) > 1:
                return self.create_medication_select_node()
            return self._route_to_status_node()

        logger.info(f"Flow: Handoff entry - context stored, proceeding to phone lookup")
        return self.create_patient_lookup_node()

    def create_other_requests_node(self) -> NodeConfig:
        """Handle non-prescription requests: scheduling, lab results, billing."""
        return NodeConfig(
            name="other_requests",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """# Goal
Route the caller to the appropriate service.

# Routing Table
| Caller needs | Action |
|--------------|--------|
| Scheduling, appointment, follow-up | call route_to_workflow with workflow="scheduling" |
| Lab results, blood work, test results | call route_to_workflow with workflow="lab_results" |
| Billing, insurance, human staff | call request_staff |
| Prescription status (changed mind) | call proceed_to_prescription_status |

# Example Flow
Caller: "I need to schedule a follow-up appointment."
→ Call route_to_workflow with workflow="scheduling", reason="follow-up appointment"

Caller: "Can I check my lab results?"
→ Call route_to_workflow with workflow="lab_results", reason="lab results inquiry"

Caller: "Actually, I wanted to check on my prescription."
→ Call proceed_to_prescription_status

# Unrecognized Intent
If unclear, ask: "I can help with scheduling, lab results, or prescriptions. Which would you like?"

# Guardrails
- Don't ask for personal information yet - the target workflow will handle verification
- Provide brief context in the reason field when routing""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                FlowsFunctionSchema(
                    name="proceed_to_prescription_status",
                    description="Caller wants to check prescription status instead.",
                    properties={
                        "mentioned_medication": {"type": "string", "description": "Medication name if mentioned"},
                    },
                    required=[],
                    handler=self._proceed_to_prescription_status_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

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
        """Final not found after max retries - transfer to staff."""
        return NodeConfig(
            name="patient_not_found_final",
            task_messages=[],
            functions=[],
            pre_actions=[{"type": "tts_say", "text": "I still couldn't find your record. Let me connect you with a colleague who can help."}],
            post_actions=[{"type": "end_conversation"}],
        )

    # ==================== Node Creators: Main Flow ====================

    def create_medication_select_node(self) -> NodeConfig:
        """Multi-rx: Ask which medication caller is asking about."""
        state = self.flow_manager.state
        prescriptions = state.get("prescriptions", [])
        attempts = state.get("medication_select_attempts", 0)

        # Build medication list for prompt
        rx_list = "\n".join([f"- {rx.get('medication_name', 'Unknown')} ({rx.get('dosage', '')})" for rx in prescriptions])

        # Different prompt based on retry attempts
        if attempts > 0:
            intro = f"I didn't catch that. Your medications on file are:\n{rx_list}\n\nWhich one are you calling about?"
        else:
            intro = f"I see you have multiple medications on file:\n{rx_list}\n\nWhich one are you calling about today?"

        return NodeConfig(
            name="medication_select",
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Determine which prescription the patient is asking about.

# Medications on File
{rx_list}

# Matching Rules
Match caller's description to medications using:
1. Exact medication name (e.g., "Ozempic")
2. Generic name (e.g., "semaglutide" → Ozempic or Wegovy)
3. Common aliases (e.g., "my weekly shot" → Ozempic/Wegovy/Mounjaro)
4. Descriptions (e.g., "the weight loss one" → match to GLP-1)

# Example Flow
You: "Which medication are you calling about today?"
Patient: "The Ozempic"
→ Call select_medication with medication_name="Ozempic"

Patient: "My weekly shot"
→ If only one injectable on file, call select_medication
→ If multiple, ask: "I see you have Ozempic and Wegovy. Which one?"

# No Match After 2 Attempts
If patient has tried twice and you still can't identify:
→ Call request_staff with reason="couldn't identify medication"

# Guardrails
- Always confirm before selecting
- Use GLP-1 aliases: Ozempic, Wegovy, Mounjaro, Zepbound, Trulicity
- Don't guess—ask for clarification if unclear""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="select_medication",
                    description="Select medication after matching caller's description. Use GLP-1 aliases for matching.",
                    properties={
                        "medication_name": {"type": "string", "description": "Name of the medication (brand name preferred)"},
                    },
                    required=["medication_name"],
                    handler=self._select_medication_handler,
                ),
                self._end_call_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": intro}] if attempts == 0 else None,
        )

    # ==================== Status Nodes (6 separate per blueprint) ====================

    def _status_base_functions(self) -> list:
        """Common functions for all status nodes."""
        return [
            FlowsFunctionSchema(
                name="proceed_to_completion",
                description="Patient satisfied with status info. Ask if anything else needed.",
                properties={},
                required=[],
                handler=self._proceed_to_completion_handler,
            ),
            self._check_another_medication_schema(),
            self._request_staff_schema(),
        ]

    def create_status_sent_node(self) -> NodeConfig:
        """Status: Prescription sent to pharmacy."""
        status_message = self._format_status_message("status_sent")
        state = self.flow_manager.state
        pharmacy_phone = state.get("pharmacy_phone", "")

        return NodeConfig(
            name="status_sent",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. The prescription was sent to the pharmacy.

# What Was Said
"{status_message}"

# Caller Options
- Satisfied → call proceed_to_completion
- Questions about pharmacy/timing → call request_staff with reason="pharmacy questions"
- Another medication → call check_another_medication

# Pharmacy Contact
{f"They can reach the pharmacy at {pharmacy_phone}." if pharmacy_phone else ""}

Do NOT offer to send again - it's already sent.""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_pending_node(self) -> NodeConfig:
        """Status: Pending prior authorization."""
        status_message = self._format_status_message("status_pending")

        return NodeConfig(
            name="status_pending",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. The prescription is pending prior authorization.

# What Was Said
"{status_message}"

# Caller Options
- Satisfied → call proceed_to_completion
- Urgent/need to expedite → call request_staff with reason="expedite prior auth"
- Another medication → call check_another_medication

Do NOT submit another request - one is already pending.""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_ready_node(self) -> NodeConfig:
        """Status: Ready for pickup at pharmacy."""
        status_message = self._format_status_message("status_ready")
        state = self.flow_manager.state
        pharmacy_phone = state.get("pharmacy_phone", "")

        return NodeConfig(
            name="status_ready",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. The prescription is ready for pickup.

# What Was Said
"{status_message}"

# Caller Options
- Satisfied → call proceed_to_completion
- Pharmacy issues → call request_staff with reason="pharmacy issues"
- Another medication → call check_another_medication

# Pharmacy Contact
{f"They can reach the pharmacy at {pharmacy_phone}." if pharmacy_phone else ""}""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_too_early_node(self) -> NodeConfig:
        """Status: Too early to refill."""
        status_message = self._format_status_message("status_too_early")

        return NodeConfig(
            name="status_too_early",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. It's too early to refill.

# What Was Said
"{status_message}"

# Caller Options
- Accepts, satisfied → call proceed_to_completion
- Exception needed (lost, broken, traveling) → call request_staff with reason="early refill exception"
- Another medication → call check_another_medication

If they need an early refill, connect with staff who can review exceptions.""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_refills_node(self) -> NodeConfig:
        """Status: Refills available - offer to send."""
        status_message = self._format_status_message("status_refills")
        state = self.flow_manager.state
        pharmacy_name = state.get("pharmacy_name", "your pharmacy")

        return NodeConfig(
            name="status_refills",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. Patient has refills available.

# What Was Said
"{status_message}"

# Caller Options
- Yes, send refill → call submit_refill
- No thanks → call proceed_to_completion
- Pharmacy change/dosage questions → call request_staff with reason="pharmacy change"
- Another medication → call check_another_medication

If patient confirms, submit the refill to {pharmacy_name}.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="submit_refill",
                    description="Patient confirmed - send refill to pharmacy.",
                    properties={},
                    required=[],
                    handler=self._submit_refill_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_completion",
                    description="Patient declines refill or is satisfied.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_completion_handler,
                ),
                self._check_another_medication_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_renewal_node(self) -> NodeConfig:
        """Status: Needs renewal/prior auth."""
        status_message = self._format_status_message("status_renewal")
        state = self.flow_manager.state
        prescribing_physician = state.get("prescribing_physician", "your doctor")

        return NodeConfig(
            name="status_renewal",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. Prescription needs renewal.

# What Was Said
"{status_message}"

# Caller Options
- Yes, submit renewal → call submit_renewal_request
- No thanks → call proceed_to_completion
- Dosage change → call request_staff with reason="dosage change request"
- Another medication → call check_another_medication

If patient confirms, submit renewal request to {prescribing_physician}.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="submit_renewal_request",
                    description="Patient confirmed - submit renewal request to physician.",
                    properties={},
                    required=[],
                    handler=self._submit_renewal_request_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_completion",
                    description="Patient declines renewal or is satisfied.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_completion_handler,
                ),
                self._check_another_medication_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def _route_to_status_node(self, prescription: dict = None) -> NodeConfig:
        """Route to appropriate status node based on prescription status."""
        if prescription:
            # Load prescription data into state
            state = self.flow_manager.state
            state["medication_name"] = prescription.get("medication_name", "")
            state["dosage"] = prescription.get("dosage", "")
            state["refill_status"] = prescription.get("status", prescription.get("refill_status", ""))
            state["refills_remaining"] = prescription.get("refills_remaining", 0)
            state["next_refill_date"] = prescription.get("next_refill_date", "")
            state["selected_prescription"] = prescription

        status_key = self._get_status_key(prescription or self.flow_manager.state)

        status_node_map = {
            "status_sent": self.create_status_sent_node,
            "status_pending": self.create_status_pending_node,
            "status_ready": self.create_status_ready_node,
            "status_too_early": self.create_status_too_early_node,
            "status_refills": self.create_status_refills_node,
            "status_renewal": self.create_status_renewal_node,
        }

        creator = status_node_map.get(status_key, self.create_status_pending_node)
        return creator()

    def create_completion_node(self) -> NodeConfig:
        state = self.flow_manager.state
        first_name = state.get("first_name", "")
        medication_name = state.get("medication_name", "")
        refill_status = state.get("refill_status", "")
        pharmacy_name = state.get("pharmacy_name", "")

        return NodeConfig(
            name="completion",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
The prescription inquiry is complete. Check if the caller needs anything else.

# Prescription Info Already Shared (for reference if asked to repeat)
- Medication: {medication_name}
- Status: {refill_status}
- Pharmacy: {pharmacy_name}

# Scenario Handling

If patient says GOODBYE / "that's all" / "bye" / "that's everything" / "no, thanks":
→ Say something warm like "You're welcome{', ' + first_name if first_name else ''}. Take care!"
→ Call end_call IMMEDIATELY

NOTE: "Thank you" or "Great, thank you" alone is NOT a goodbye signal.
→ After "thank you", ask: "Is there anything else I can help with?"
→ Only end_call if they respond with clear goodbye like "No, that's all" or "Bye"

If patient asks about ANOTHER MEDICATION, PRESCRIPTION, REFILL:
→ Call check_another_medication IMMEDIATELY

Examples:
- "What about my other prescription?" → check_another_medication
- "Can you also check my [medication]?" → check_another_medication

If patient asks to SCHEDULE an APPOINTMENT or FOLLOW-UP:
→ Call route_to_workflow with workflow="scheduling" IMMEDIATELY
→ Do NOT speak first - the scheduling workflow will handle it

Examples:
- "I need to schedule a follow-up appointment" → route_to_workflow(scheduling)
- "Can I book an appointment with Dr. Williams?" → route_to_workflow(scheduling)

If patient asks about LAB RESULTS or BLOOD WORK:
→ Call route_to_workflow with workflow="lab_results" IMMEDIATELY

If patient asks for a HUMAN or has BILLING questions:
→ Say "Let me connect you with someone who can help."
→ Call request_staff

# Example Flow
You: "Is there anything else I can help you with today?"

Caller: "Actually yes, I need to schedule a follow-up appointment with Dr. Williams."
→ Call route_to_workflow with workflow="scheduling", reason="follow-up to discuss medications"

Caller: "No, that's everything. Thank you!"
→ "You're welcome{', ' + first_name if first_name else ''}. Thank you for calling {self.organization_name}. Take care!"
→ Call end_call

# Guardrails
- The caller is already verified - no need to re-verify for scheduling or lab results
- Include relevant context in the reason field when routing
- Keep responses brief and warm
- If caller is frustrated or asks for a human, call request_staff""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                self._check_another_medication_schema(),
                self._end_call_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": "Is there anything else I can help you with today?"}],
        )

    # ==================== Node Creators: Bridge/Utility ====================

    def _create_post_workflow_node(self, target_flow, workflow_type: str, entry_method, transition_message: str = "") -> NodeConfig:
        async def proceed_handler(args, flow_manager):
            return None, entry_method()

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
                "content": """The transfer didn't go through. Offer alternatives:
- "You can call the clinic directly during business hours."
- "I can make a note on your file for staff to follow up."

If caller accepts an alternative or says goodbye:
→ Call end_call

If caller has a question you can answer:
→ Answer it, then ask if there's anything else""",
            }],
            functions=[self._end_call_schema()],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": "I apologize, the transfer didn't go through."}],
        )

    def _create_verification_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="verification_failed",
            task_messages=[],
            functions=[],
            pre_actions=[{"type": "tts_say", "text": "I'm sorry, I couldn't find your record. Let me transfer you to a staff member who can assist you. One moment please."}],
            post_actions=[{"type": "end_conversation"}],
        )

    # ==================== Handlers: Phone Lookup Verification ====================

    async def _lookup_by_phone_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        phone_digits = self._normalize_phone(args.get("phone_number", ""))
        logger.info(f"Flow: Looking up phone: {self._phone_last4(phone_digits)}")

        # Store for patient_not_found node display
        flow_manager.state["_last_lookup_phone"] = phone_digits

        if patient := await get_async_patient_db().find_patient_by_phone(phone_digits, self.organization_id, "prescription_status"):
            # Store lookup record for DOB verification
            flow_manager.state["_lookup_record"] = {
                "patient_id": patient.get("patient_id"),
                "first_name": patient.get("first_name", ""),
                "last_name": patient.get("last_name", ""),
                "date_of_birth": patient.get("date_of_birth", ""),
                "phone_number": patient.get("phone_number", ""),
                "medication_name": patient.get("medication_name", ""),
                "dosage": patient.get("dosage", ""),
                "prescribing_physician": patient.get("prescribing_physician", ""),
                "refill_status": patient.get("refill_status", ""),
                "refills_remaining": patient.get("refills_remaining", 0),
                "last_filled_date": patient.get("last_filled_date", ""),
                "next_refill_date": patient.get("next_refill_date", ""),
                "pharmacy_name": patient.get("pharmacy_name", ""),
                "pharmacy_phone": patient.get("pharmacy_phone", ""),
                "pharmacy_address": patient.get("pharmacy_address", ""),
                "prescriptions": patient.get("prescriptions", []),
            }
            logger.info("Flow: Found record, requesting DOB")
            return None, self.create_verify_dob_node()

        # Not found - offer retry or transfer (max 2 attempts)
        flow_manager.state["lookup_attempts"] = flow_manager.state.get("lookup_attempts", 0) + 1
        if flow_manager.state["lookup_attempts"] >= 2:
            logger.info("Flow: No patient found after 2 attempts - transferring to staff")
            await self._initiate_sip_transfer(flow_manager)
            return None, self.create_patient_not_found_final_node()

        logger.info("Flow: No patient found - offering retry")
        flow_manager.state["_last_lookup_dob"] = ""
        return None, self.create_patient_not_found_node()

    async def _retry_lookup_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        """Retry patient lookup with new phone/DOB - increments lookup_attempts."""
        phone_digits = self._normalize_phone(args.get("phone_number", ""))
        provided_dob = args.get("date_of_birth", "")

        logger.info(f"Flow: Retry lookup with phone: {self._phone_last4(phone_digits)}")

        if patient := await get_async_patient_db().find_patient_by_phone(phone_digits, self.organization_id, "prescription_status"):
            # Found patient - verify DOB if provided, otherwise ask for it
            flow_manager.state["_lookup_record"] = {
                "patient_id": patient.get("patient_id"),
                "first_name": patient.get("first_name", ""),
                "last_name": patient.get("last_name", ""),
                "date_of_birth": patient.get("date_of_birth", ""),
                "phone_number": patient.get("phone_number", ""),
                "medication_name": patient.get("medication_name", ""),
                "dosage": patient.get("dosage", ""),
                "prescribing_physician": patient.get("prescribing_physician", ""),
                "refill_status": patient.get("refill_status", ""),
                "refills_remaining": patient.get("refills_remaining", 0),
                "last_filled_date": patient.get("last_filled_date", ""),
                "next_refill_date": patient.get("next_refill_date", ""),
                "pharmacy_name": patient.get("pharmacy_name", ""),
                "pharmacy_phone": patient.get("pharmacy_phone", ""),
                "pharmacy_address": patient.get("pharmacy_address", ""),
                "prescriptions": patient.get("prescriptions", []),
            }
            logger.info("Flow: Found record on retry, requesting DOB")
            return None, self.create_verify_dob_node()

        # Still not found after retry - go to final not found
        logger.info("Flow: Still no patient found on retry - transferring to staff")
        await self._initiate_sip_transfer(flow_manager)
        return None, self.create_patient_not_found_final_node()

    async def _verify_dob_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        """Verify DOB and route using software logic per blueprint."""
        provided = parse_natural_date(args.get("date_of_birth", "").strip())
        lookup = flow_manager.state.get("_lookup_record", {})
        stored = lookup.get("date_of_birth", "")
        logger.info(f"Flow: Verifying DOB - provided: {provided}, stored: {stored}")

        # Store for patient_not_found display
        flow_manager.state["_last_lookup_dob"] = provided or args.get("date_of_birth", "").strip()

        # DOB mismatch - go to patient_not_found (offer retry)
        if not provided or provided != stored:
            logger.warning("Flow: DOB mismatch")
            flow_manager.state.pop("_lookup_record", None)
            flow_manager.state["lookup_attempts"] = flow_manager.state.get("lookup_attempts", 0) + 1

            if flow_manager.state["lookup_attempts"] >= 2:
                await self._initiate_sip_transfer(flow_manager)
                return None, self.create_patient_not_found_final_node()

            return None, self.create_patient_not_found_node()

        # DOB matches - mark verified and load domain data
        flow_manager.state["identity_verified"] = True
        flow_manager.state["patient_id"] = lookup.get("patient_id")
        flow_manager.state["first_name"] = lookup.get("first_name", "")
        flow_manager.state["last_name"] = lookup.get("last_name", "")
        flow_manager.state["date_of_birth"] = stored
        flow_manager.state["phone_number"] = lookup.get("phone_number", "")
        flow_manager.state["patient_name"] = f"{lookup.get('first_name', '')} {lookup.get('last_name', '')}".strip()

        # Load domain-specific data for prescriptions
        flow_manager.state["medication_name"] = lookup.get("medication_name", "")
        flow_manager.state["dosage"] = lookup.get("dosage", "")
        flow_manager.state["prescribing_physician"] = lookup.get("prescribing_physician", "")
        flow_manager.state["refill_status"] = lookup.get("refill_status", "")
        flow_manager.state["refills_remaining"] = lookup.get("refills_remaining", 0)
        flow_manager.state["last_filled_date"] = lookup.get("last_filled_date", "")
        flow_manager.state["next_refill_date"] = lookup.get("next_refill_date", "")
        flow_manager.state["pharmacy_name"] = lookup.get("pharmacy_name", "")
        flow_manager.state["pharmacy_phone"] = lookup.get("pharmacy_phone", "")
        flow_manager.state["pharmacy_address"] = lookup.get("pharmacy_address", "")
        flow_manager.state["prescriptions"] = lookup.get("prescriptions", [])

        flow_manager.state.pop("_lookup_record", None)
        first_name = lookup.get("first_name", "")
        logger.info(f"Flow: DOB verified for {first_name}")

        # Software routing per blueprint:
        prescriptions = flow_manager.state.get("prescriptions", [])
        mentioned_medication = flow_manager.state.get("mentioned_medication")

        # 1. Single prescription - route directly to status node
        if len(prescriptions) == 1:
            selected = prescriptions[0]
            flow_manager.state["selected_prescription"] = selected
            # Load prescription data into state
            flow_manager.state["medication_name"] = selected.get("medication_name", "")
            flow_manager.state["dosage"] = selected.get("dosage", "")
            flow_manager.state["refill_status"] = selected.get("status", selected.get("refill_status", ""))
            flow_manager.state["refills_remaining"] = selected.get("refills_remaining", 0)
            flow_manager.state["next_refill_date"] = selected.get("next_refill_date", "")

            logger.info(f"Flow: Single prescription - routing to status node")
            return None, self._route_to_status_node(selected)

        # 2. Multiple prescriptions + mentioned medication match
        if mentioned_medication and len(prescriptions) > 1:
            match = self._find_medication_match(mentioned_medication, prescriptions)
            if match:
                flow_manager.state["selected_prescription"] = match
                flow_manager.state["medication_name"] = match.get("medication_name", "")
                flow_manager.state["dosage"] = match.get("dosage", "")
                flow_manager.state["refill_status"] = match.get("status", match.get("refill_status", ""))
                flow_manager.state["refills_remaining"] = match.get("refills_remaining", 0)
                flow_manager.state["next_refill_date"] = match.get("next_refill_date", "")

                logger.info(f"Flow: Matched mentioned medication '{mentioned_medication}' - routing to status node")
                return None, self._route_to_status_node(match)

        # 3. Multiple prescriptions, no match - go to medication_select
        logger.info(f"Flow: Multiple prescriptions, no match - routing to medication_select")
        return None, self.create_medication_select_node()

    async def _initiate_sip_transfer(self, flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        """Initiate SIP transfer for protected flow verification failures."""
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
            if patient_id:
                await self._try_db_update(patient_id, "update_patient", {"call_status": "Transferred"}, error_msg="Error updating call status")

            return None, self.create_transfer_initiated_node()

        except Exception:
            logger.exception("SIP transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False
            return None, self.create_transfer_failed_node()

    # ==================== Handlers: Routing ====================

    async def _proceed_to_prescription_status_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        """Route to prescription status flow, capturing mentioned medication if provided."""
        mentioned_medication = args.get("mentioned_medication", "")
        if mentioned_medication:
            flow_manager.state["mentioned_medication"] = mentioned_medication
            logger.info(f"Flow: Proceeding to prescription status with mentioned medication: {mentioned_medication}")
        else:
            logger.info("Flow: Proceeding to prescription status")

        return None, self.create_patient_lookup_node()

    async def _proceed_to_other_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        """Route to other requests node for scheduling/lab results."""
        logger.info("Flow: Proceeding to other requests")
        return None, self.create_other_requests_node()

    # ==================== Handlers: Prescription Actions ====================

    async def _select_medication_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        """Select medication using GLP-1 alias matching and route to status node."""
        medication_name = args.get("medication_name", "").strip()
        logger.info(f"Flow: Selected medication: {medication_name}")

        prescriptions = flow_manager.state.get("prescriptions", [])

        # Use GLP-1 medication matching
        selected_rx = self._find_medication_match(medication_name, prescriptions)

        # Fallback to simple matching if GLP-1 matching didn't work
        if not selected_rx:
            for rx in prescriptions:
                if medication_name.lower() in rx.get("medication_name", "").lower():
                    selected_rx = rx
                    break

        if not selected_rx:
            # Increment attempt counter
            flow_manager.state["medication_select_attempts"] = flow_manager.state.get("medication_select_attempts", 0) + 1

            if flow_manager.state["medication_select_attempts"] >= 2:
                logger.info("Flow: Couldn't identify medication after 2 attempts - transferring to staff")
                return None, await self._request_staff_handler({"reason": "couldn't identify medication"}, flow_manager)

            return f"I couldn't find a prescription matching '{medication_name}'. Could you clarify which medication?", self.create_medication_select_node()

        # Load prescription data into state
        flow_manager.state["medication_name"] = selected_rx.get("medication_name", "")
        flow_manager.state["dosage"] = selected_rx.get("dosage", "")
        flow_manager.state["prescribing_physician"] = selected_rx.get("prescribing_physician", "")
        flow_manager.state["refill_status"] = selected_rx.get("status", selected_rx.get("refill_status", ""))
        flow_manager.state["refills_remaining"] = selected_rx.get("refills_remaining", 0)
        flow_manager.state["last_filled_date"] = selected_rx.get("last_filled_date", "")
        flow_manager.state["next_refill_date"] = selected_rx.get("next_refill_date", "")
        flow_manager.state["selected_prescription"] = selected_rx

        # Route to appropriate status node
        return None, self._route_to_status_node(selected_rx)

    async def _submit_refill_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        pharmacy_name = args.get("pharmacy_name", flow_manager.state.get("pharmacy_name", "your pharmacy"))
        medication_name = flow_manager.state.get("medication_name", "")

        logger.info(f"Flow: Submitting refill for {medication_name} to {pharmacy_name}")

        patient_id = flow_manager.state.get("patient_id")
        if patient_id:
            await self._try_db_update(
                patient_id, "update_patient",
                {"refill_requested": True, "refill_pharmacy": pharmacy_name, "call_status": "Completed"},
                error_msg="Error saving refill request"
            )

        return f"I've submitted the refill request to {pharmacy_name}. They should have it ready within 2 to 4 hours.", self.create_completion_node()

    async def _submit_renewal_request_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        physician = flow_manager.state.get("prescribing_physician", "your doctor")
        medication_name = flow_manager.state.get("medication_name", "")
        pharmacy_name = flow_manager.state.get("pharmacy_name", "your pharmacy")

        logger.info(f"Flow: Submitting renewal request for {medication_name} to {physician}")

        patient_id = flow_manager.state.get("patient_id")
        if patient_id:
            await self._try_db_update(
                patient_id, "update_patient",
                {"renewal_requested": True, "renewal_physician": physician, "call_status": "Completed"},
                error_msg="Error saving renewal request"
            )

        return f"I've submitted the refill request to {physician} for review. Once approved, the prescription will be sent to {pharmacy_name}. You should hear back within 1 to 2 business days.", self.create_completion_node()

    async def _check_another_medication_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        logger.info("Flow: Checking another prescription")
        prescriptions = flow_manager.state.get("prescriptions", [])

        if len(prescriptions) > 1:
            return "", self.create_medication_select_node()
        else:
            return "I only see one prescription on file for you. Is there something else I can help you with?", self.create_completion_node()

    async def _proceed_to_completion_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Proceeding to completion")
        return None, self.create_completion_node()

    # ==================== Handlers: Workflow Routing ====================

    async def _route_to_workflow_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")

        flow_manager.state["routed_to"] = f"{workflow} (AI)"
        logger.info(f"Flow: Routing to {workflow} workflow - reason: {reason}")

        if workflow not in self.WORKFLOW_FLOWS:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

        module_path, class_name, entry_method_name = self.WORKFLOW_FLOWS[workflow]
        module = importlib.import_module(module_path)
        FlowClass = getattr(module, class_name)

        target_flow = FlowClass(
            call_data=self.call_data, session_id=self.session_id, flow_manager=flow_manager,
            main_llm=self.main_llm, context_aggregator=self.context_aggregator,
            transport=self.transport, pipeline=self.pipeline,
            organization_id=self.organization_id, cold_transfer_config=self.cold_transfer_config,
        )

        if flow_manager.state.get("identity_verified"):
            first_name = flow_manager.state.get("first_name", "")
            if workflow == "scheduling":
                flow_manager.state["appointment_reason"] = reason
                flow_manager.state["appointment_type"] = "Returning Patient"
            msg = f"Let me help with that, {first_name}!" if first_name else "Let me help with that!"
            entry_method = getattr(target_flow, entry_method_name)
            return None, self._create_post_workflow_node(target_flow, workflow, entry_method, msg)

        return None, await target_flow.create_handoff_entry_node(context=reason)

    # ==================== Handlers: Transfers ====================

    async def _request_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        patient_confirmed = args.get("patient_confirmed", False)
        reason = args.get("reason", "general inquiry")

        logger.info(f"Flow: Staff transfer requested - reason: {reason}, confirmed: {patient_confirmed}")

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

            patient_id = flow_manager.state.get("patient_id")
            if patient_id:
                await self._try_db_update(patient_id, "update_patient", {"call_status": "Transferred"}, error_msg="Error updating call status")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception("Cold transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False
            return None, self.create_transfer_failed_node()

    # ==================== Handlers: End Call ====================

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        patient_id = flow_manager.state.get("patient_id")
        session_db = get_async_session_db()
        logger.info("Flow: Ending call")

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
                    "refill_requested": flow_manager.state.get("refill_requested", False),
                    "status_communicated": True,
                }, self.organization_id)

        except Exception as e:
            logger.exception("Error in end_call_handler")
            try:
                await session_db.update_session(self.session_id, {"status": "failed"}, self.organization_id)
            except Exception as db_error:
                logger.error(f"Failed to update session status: {db_error}")

        return None, NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Thank the patient and say goodbye."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )
