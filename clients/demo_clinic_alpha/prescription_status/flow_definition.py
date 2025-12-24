import importlib
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
        flow = PrescriptionStatusFlow(
            patient_data=patient_data,
            flow_manager=_MockFlowManager(),
            main_llm=None,
        )
        greeting_node = flow.create_greeting_node()

        messages = []
        for msg in greeting_node.role_messages or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        for msg in greeting_node.task_messages or []:
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

    WORKFLOW_FLOWS = {
        "lab_results": ("clients.demo_clinic_alpha.lab_results.flow_definition", "LabResultsFlow", "create_results_node"),
        "scheduling": ("clients.demo_clinic_alpha.patient_scheduling.flow_definition", "PatientSchedulingFlow", "create_scheduling_node"),
    }

    IDENTITY_FIELDS = ["patient_id", "patient_name", "first_name", "last_name", "date_of_birth", "medical_record_number", "phone_number"]
    RX_FIELDS = ["medication_name", "dosage", "prescribing_physician", "refill_status", "last_filled_date", "next_refill_date",
                 "pharmacy_name", "pharmacy_phone", "pharmacy_address"]

    # ==================== Initialization ====================

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
        self._init_state()

    def _init_state(self):
        state = self.flow_manager.state
        for field in self.IDENTITY_FIELDS:
            state[field] = state.get(field) or self.patient_data.get(field, "" if field != "patient_id" else None)
        for field in self.RX_FIELDS:
            state[field] = self.patient_data.get(field, "")
        state["refills_remaining"] = self.patient_data.get("refills_remaining", 0)
        state["prescriptions"] = self.patient_data.get("prescriptions", [])
        state["identity_verified"] = state.get("identity_verified", False)

    # ==================== Helpers: Database ====================

    async def _try_db_update(self, patient_id: str, method: str, *args, error_msg: str = "DB update error"):
        if not patient_id:
            return
        try:
            db = get_async_patient_db()
            await getattr(db, method)(patient_id, *args, self.organization_id)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")

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
- You MUST verify patient identity before discussing any prescription information. This step is important.
- Ask for full name AND date of birth
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
                "urgent": {"type": "boolean", "description": "True for urgent/medical concerns"},
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

    def _check_another_prescription_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="check_another_prescription",
            description="Patient asks about another medication/prescription/refill.",
            properties={},
            required=[],
            handler=self._check_another_prescription_handler,
        )

    # ==================== Node Creators: Entry Points ====================

    def create_greeting_node(self) -> NodeConfig:
        greeting_text = f"Thank you for calling {self.organization_name}. This is Jamie. How can I help you today?"

        return NodeConfig(
            name="greeting",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """# Goal
Determine what the caller needs and route appropriately. This step is important.

# Expected Responses
If caller mentions prescription, refill, or medication:
→ Call start_verification immediately

If caller needs something else (appointments, billing, medical questions):
→ Say "Let me connect you with someone who can help with that." and call request_staff

# Example Flow
Caller: "Hi, I need to check on a prescription refill."
→ Call start_verification

Caller: "I need to schedule an appointment."
→ "Let me connect you with someone who can help with that."
→ Call request_staff

# Guardrails
- Do NOT ask for any personal information yet (name, DOB, etc.)
- Do NOT discuss prescriptions until identity is verified
- Route to verification as soon as caller mentions prescription/refill/medication
- Stay on topic: prescription inquiries only

# Error Handling
If you don't understand the caller:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never guess or assume what they need""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_verification",
                    description="Caller asks about prescription/refill/medication. Transitions to identity verification.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_verification_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": greeting_text}],
        )

    def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        context_lower = context.lower()
        if "lisinopril" in context_lower:
            self.flow_manager.state["medication_name"] = "lisinopril"
        if "prior auth" in context_lower:
            self.flow_manager.state["issue_type"] = "prior_authorization"

        if self.flow_manager.state.get("identity_verified"):
            first_name = self.flow_manager.state.get("first_name", "")
            logger.info(f"Flow: Caller already verified as {first_name}, skipping verification")
            return self.create_status_node()

        logger.info(f"Flow: Handoff entry - context stored, proceeding to verification")
        return self.create_verification_node()

    # ==================== Node Creators: Main Flow ====================

    def create_verification_node(self) -> NodeConfig:
        state = self.flow_manager.state
        first_name = state.get("first_name", "")
        last_name = state.get("last_name", "")
        stored_name = f"{first_name} {last_name}".strip() if first_name or last_name else state.get("patient_name", "")
        stored_dob = state.get("date_of_birth", "")

        return NodeConfig(
            name="verification",
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Verify the caller's identity before sharing any prescription information. This step is important.

# Patient Record on File
- Name: {stored_name}
- Date of Birth: {stored_dob}

# Third-Party Caller Detection (CRITICAL)
If caller indicates they are NOT the patient, you MUST transfer to staff:
- "I'm calling for my mother/father/spouse/parent"
- "I'm calling on behalf of..."
- "I manage their medications"
- "They can't come to the phone"

Response: "For privacy reasons, I can only discuss prescription information directly with the patient or with documented authorization on file. Let me connect you with a staff member who can help."
→ Call request_staff immediately

# Verification Steps (only if caller IS the patient)
1. Ask for their first and last name
2. Ask for their date of birth
3. Compare against the record on file
4. Call verify_identity with the information they provide

# Example Flow
The pre-action already asked for their name.
Caller: "Jennifer Martinez"
You: "Thank you, Jennifer. And what is your date of birth?"
Caller: "September 12, 1980"
→ Call verify_identity with name="Jennifer Martinez" and date_of_birth="September 12, 1980"

# If caller already provided BOTH name and DOB
Caller: "I'm Jennifer Martinez, born September 12, 1980"
→ Call verify_identity immediately with name="Jennifer Martinez" and date_of_birth="September 12, 1980"

# Data Normalization
**Dates** (spoken → written):
- "september twelfth nineteen eighty" → "September 12, 1980"
- "nine twelve eighty" → "September 12, 1980"

Always normalize dates before calling verify_identity.

# Guardrails
- Collect BOTH name AND date of birth before calling verify_identity. This step is important.
- Do NOT reveal any patient information during verification
- Do NOT say whether the name or DOB matches until both are collected
- Be patient if caller needs to repeat information
- If caller refuses to verify, explain it's required for privacy and offer to transfer to staff
- If caller is a third party, transfer to staff immediately

# Error Handling
If you miss information:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never guess or make up values
- If caller is unclear, ask for clarification: "Could you spell that for me?" """,
            }],
            functions=[
                FlowsFunctionSchema(
                    name="verify_identity",
                    description="After collecting BOTH name AND date of birth. Verifies against stored record.",
                    properties={
                        "name": {"type": "string", "description": "Caller's full name (first and last)"},
                        "date_of_birth": {"type": "string", "description": "Date of birth in natural format (e.g., 'September 12, 1980')"},
                    },
                    required=["name", "date_of_birth"],
                    handler=self._verify_identity_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
        )

    def create_medication_identification_node(self) -> NodeConfig:
        state = self.flow_manager.state
        prescriptions = state.get("prescriptions", [])

        multi_rx_context = ""
        if len(prescriptions) > 1:
            rx_list = "\n".join([f"- {rx.get('medication_name', 'Unknown')} ({rx.get('dosage', '')})" for rx in prescriptions])
            multi_rx_context = f"""# Multiple Prescriptions on File
{rx_list}

Ask which medication they're calling about. If they describe it vaguely, help identify it:
"I see you have Amoxicillin and Chlorhexidine on file. Which one are you calling about?"
"""

        return NodeConfig(
            name="medication_identification",
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Determine which prescription the patient is asking about. This step is important.

{multi_rx_context}

# Single Prescription Flow
If patient only has one prescription, confirm it:
"I see you have a prescription for [medication]. Is that the one you're calling about?"

# Identification Strategies
If patient describes medication vaguely:
- "the antibiotic" → match to antibiotic-type medications
- "the mouth rinse" → match to oral rinse medications
- "the one Dr. Smith prescribed" → match by prescribing physician

# When to Call Functions
Once medication is identified → call select_medication with the medication name

# Example Flow
You: "Which medication are you calling about today?"
Patient: "The Chlorhexidine mouth rinse."
→ Call select_medication with medication_name="Chlorhexidine"

Patient: "I'm not sure of the name. It's the antibiotic from my tooth extraction."
You: "I see you have Amoxicillin 500mg prescribed by Dr. Park following your extraction. Is that the one?"
Patient: "Yes, that's it."
→ Call select_medication with medication_name="Amoxicillin"

# Data Normalization
Medication names may be spoken differently:
- "Chlorhexidine" / "that mouth rinse" / "the rinse" → match to Chlorhexidine
- Brand vs generic names should match when possible
- Descriptions like "the antibiotic" → match by medication type

# Guardrails
- Never guess which medication—always confirm with the patient
- If unable to identify medication, ask for more details
- Stay on topic: medication identification only

# Error Handling
If you don't understand the medication name:
- Ask naturally: "I'm sorry, could you spell that for me?" or "Could you describe it?"
- Never assume—always confirm before proceeding""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="select_medication",
                    description="Select medication patient is asking about after confirmation.",
                    properties={
                        "medication_name": {"type": "string", "description": "Name of the medication selected"},
                    },
                    required=["medication_name"],
                    handler=self._select_medication_handler,
                ),
                self._end_call_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

    def create_status_node(self) -> NodeConfig:
        state = self.flow_manager.state
        medication_name = state.get("medication_name", "Unknown")
        dosage = state.get("dosage", "")
        prescribing_physician = state.get("prescribing_physician", "your doctor")
        refill_status = state.get("refill_status", "Unknown")
        refills_remaining = state.get("refills_remaining", 0)
        last_filled_date = state.get("last_filled_date", "Unknown")
        next_refill_date = state.get("next_refill_date", "")
        pharmacy_name = state.get("pharmacy_name", "your pharmacy")
        pharmacy_phone = state.get("pharmacy_phone", "")
        pharmacy_address = state.get("pharmacy_address", "")

        return NodeConfig(
            name="status",
            task_messages=[{
                "role": "system",
                "content": f"""# CRITICAL: SPEAK FIRST
You MUST immediately share the prescription status below. Do NOT call any function before speaking.
Say the status info FIRST, then ask if there's anything else you can help with.

# Current Prescription Information
- Medication: {medication_name} {dosage}
- Prescribing Physician: {prescribing_physician}
- Refill Status: {refill_status}
- Refills Remaining: {refills_remaining}
- Last Filled: {last_filled_date}
- Next Eligible Refill: {next_refill_date}

# Pharmacy on File
- Name: {pharmacy_name}
- Phone: {pharmacy_phone}
- Address: {pharmacy_address}

# Scenario Handling

## Already Sent to Pharmacy (status is "Sent to Pharmacy")
1. Confirm: "Your refill for {medication_name} has already been sent to {pharmacy_name}."
2. Provide pharmacy contact: "You can reach them at {pharmacy_phone} to check if it's ready for pickup."
3. Do NOT offer to send it again - it's already been sent
4. If patient wants to check on timing, suggest calling the pharmacy directly

## Pending Doctor Approval (status is "Pending Doctor Approval")
1. Explain: "I see your refill request for {medication_name} is currently awaiting approval from {prescribing_physician}."
2. Provide timeline: "The doctor's office typically reviews these within 1 to 2 business days."
3. Do NOT offer to submit another renewal request - one is already pending
4. If patient expresses urgency or needs it sooner → call request_staff to connect with someone who can help expedite

## Refills Available (refills_remaining > 0, status is Active/Ready)
1. Confirm medication details
2. Offer to send refill: "You have {refills_remaining} refills remaining. Would you like me to send the refill to your pharmacy?"
3. If yes → confirm pharmacy → call submit_refill
4. Inform: "Your pharmacy should have it ready within 2 to 4 hours."

## No Refills Remaining (refills_remaining = 0, status is NOT "Pending Doctor Approval")
1. Explain: "This prescription has no refills remaining."
2. Offer to request renewal: "I can submit a refill request to {prescribing_physician} for review. This typically takes 1 to 2 business days."
3. If yes → call submit_renewal_request

## Too Early to Refill (status is "Too Early")
1. Explain: "Based on your last fill date, it's a bit early for a refill."
2. Provide next eligible date: "You'll be eligible for a refill on {next_refill_date}."
3. If patient insists they need it early (vacation, running low, etc.) → call request_staff to connect with someone who can review the exception request

## Prescription Expired or Completed
1. Explain the prescription is no longer active
2. Recommend scheduling follow-up if needed

# Pharmacy Changes
If patient wants to change pharmacy:
→ "I'll need to connect you with a staff member who can update your pharmacy on file."
→ Call request_staff
Do NOT update pharmacy directly - staff must verify and process pharmacy changes

# Guardrails
- Never provide medical advice about medications
- Record information immediately via function calls. This step is important.
- If patient asks about dosage changes or medical concerns, recommend speaking with their doctor

# Error Handling
If you don't understand the patient's request:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never guess what action to take—always confirm
- If function call fails, continue naturally without mentioning technical issues""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="submit_refill",
                    description="Submit refill when patient has refills available AND confirms.",
                    properties={
                        "pharmacy_name": {"type": "string", "description": "Pharmacy name (use current if not changed)"},
                    },
                    required=["pharmacy_name"],
                    handler=self._submit_refill_handler,
                ),
                FlowsFunctionSchema(
                    name="submit_renewal_request",
                    description="Submit renewal to physician when no refills remaining AND patient wants renewal.",
                    properties={},
                    required=[],
                    handler=self._submit_renewal_request_handler,
                ),
                self._check_another_prescription_schema(),
                FlowsFunctionSchema(
                    name="proceed_to_completion",
                    description="Patient satisfied with prescription info. Transitions to ask 'anything else?'",
                    properties={},
                    required=[],
                    handler=self._proceed_to_completion_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

    def create_closing_node(self) -> NodeConfig:
        state = self.flow_manager.state
        first_name = state.get("first_name", "")

        return NodeConfig(
            name="closing",
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Wrap up the call professionally.

# Closing Flow
1. Ask: "Is there anything else I can help you with?"
2. If no → thank them and say goodbye
3. If yes → handle the request appropriately

# CRITICAL: Another Medication/Prescription Question
If patient asks about ANOTHER MEDICATION, PRESCRIPTION, REFILL, or any medicine:
→ Call check_another_prescription IMMEDIATELY

Examples that require check_another_prescription:
- "Can you check on my other prescription?"
- "What about my fluoride rinse?"
- "Can you also look at my blood pressure medication?"
- "I also need to ask about [any medication name]"

# CRITICAL: Scheduling/Appointment Requests
If patient asks to SCHEDULE an APPOINTMENT or FOLLOW-UP:
→ Call route_to_workflow with workflow="scheduling" IMMEDIATELY

Examples that require route_to_workflow(scheduling):
- "I need to schedule a follow-up appointment"
- "Can I schedule an appointment with Dr. Williams?"
- "I want to book a visit"

DO NOT use request_staff for scheduling - use route_to_workflow instead.

# Lab Results Requests
If they ask about LAB RESULTS or BLOOD WORK (test results, not medications):
→ Call route_to_workflow with workflow="lab_results"

# Example Goodbye
You: "Is there anything else I can help you with today?"
Patient: "No, that's everything. Thank you."
You: "You're welcome{', ' + first_name if first_name else ''}. Thank you for calling {self.organization_name}. Have a great day."
→ Call end_call

# Guardrails
- Always ask if there's anything else before ending
- If patient asks about another medication → check_another_prescription
- If patient asks about scheduling or appointments → route_to_workflow(scheduling)
- If patient asks about lab results → route_to_workflow(lab_results)
- Keep the closing warm and professional

# Error Handling
If you don't understand the patient's response:
- Ask naturally: "I'm sorry, did you need help with something else?"
- Never assume they're done—always confirm""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                self._check_another_prescription_schema(),
                self._end_call_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
        )

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
→ Call check_another_prescription IMMEDIATELY

Examples:
- "What about my other prescription?" → check_another_prescription
- "Can you also check my [medication]?" → check_another_prescription

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
- The caller's identity is already verified - no need to re-verify for scheduling or lab results
- Include relevant context in the reason field when routing
- Keep responses brief and warm
- If caller is frustrated or asks for a human, call request_staff""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                self._check_another_prescription_schema(),
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
        transfer_reason = self.flow_manager.state.get("transfer_reason", "")

        if transfer_reason == "expedite":
            alternatives = """PROACTIVELY OFFER THESE ALTERNATIVES:
1. "I can note on your file that this is urgent, so when the doctor reviews it they'll see the priority."
2. "You can also call the clinic directly at your convenience and ask to speak with the prescription team."
3. "Would you like me to make a note about the urgency, or is there anything else I can help with?"

Do NOT keep offering to retry the transfer. Proactively suggest one of the alternatives above."""
        elif transfer_reason == "pharmacy_change":
            alternatives = """PROACTIVELY OFFER THESE ALTERNATIVES:
1. "You can call the clinic directly to update your pharmacy on file."
2. "Would you like me to make a note that you need a pharmacy change, so staff can follow up?"

Do NOT keep offering to retry the transfer. Proactively suggest one of the alternatives above."""
        else:
            alternatives = """PROACTIVELY OFFER THESE ALTERNATIVES:
1. "You can call the clinic directly during business hours."
2. "I can make a note on your file for staff to follow up."
3. "Is there anything else I can help you with in the meantime?"

Do NOT keep offering to retry the transfer. Proactively suggest one of the alternatives above."""

        return NodeConfig(
            name="transfer_failed",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": f"""The transfer didn't go through. Apologize and offer alternatives.

{alternatives}

If caller accepts an alternative (note on file, callback, etc.):
→ Acknowledge and call end_call

If caller says goodbye or wants to end call:
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
            pre_actions=[{"type": "tts_say", "text": "I'm sorry, I wasn't able to verify your identity. For your security, I'll need to transfer you to a staff member who can assist you. One moment please."}],
            post_actions=[{"type": "end_conversation"}],
        )

    # ==================== Handlers: Verification ====================

    async def _proceed_to_verification_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        logger.info("Flow: Proceeding to verification")
        return "For privacy and security, I need to verify your identity first. May I have your first and last name?", self.create_verification_node()

    async def _verify_identity_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        provided_name = args.get("name", "").strip()
        provided_dob = args.get("date_of_birth", "").strip()

        first_name = flow_manager.state.get("first_name", "")
        last_name = flow_manager.state.get("last_name", "")
        stored_name = f"{first_name} {last_name}".strip() if first_name or last_name else flow_manager.state.get("patient_name", "")
        stored_dob = flow_manager.state.get("date_of_birth", "")

        provided_name_normalized = self._normalize_name(provided_name)
        stored_name_normalized = self._normalize_name(stored_name)
        provided_dob_normalized = self._normalize_dob(provided_dob)
        stored_dob_normalized = self._normalize_dob(stored_dob)

        name_match = provided_name_normalized == stored_name_normalized if stored_name_normalized else False
        dob_match = provided_dob_normalized == stored_dob_normalized if stored_dob_normalized else False

        logger.info(f"Flow: Identity verification - name_match={name_match}, dob_match={dob_match}")

        caller_first_name = provided_name.strip().split()[0].title() if provided_name.strip() else "there"

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

            logger.info(f"Flow: Identity verified for {caller_first_name}")

            prescriptions = flow_manager.state.get("prescriptions", [])
            if len(prescriptions) > 1:
                return f"Thank you, {caller_first_name}. I've verified your identity. Which medication are you calling about today?", self.create_medication_identification_node()

            medication_name = flow_manager.state.get("medication_name", "your medication")
            refill_status = flow_manager.state.get("refill_status", "")
            pharmacy_name = flow_manager.state.get("pharmacy_name", "your pharmacy")
            pharmacy_phone = flow_manager.state.get("pharmacy_phone", "")
            prescribing_physician = flow_manager.state.get("prescribing_physician", "your doctor")
            next_refill_date = flow_manager.state.get("next_refill_date", "")

            status_lower = refill_status.lower() if refill_status else ""

            if status_lower == "sent to pharmacy":
                message = f"Thank you, {caller_first_name}. I found your record. Your refill for {medication_name} has already been sent to {pharmacy_name}."
                if pharmacy_phone:
                    message += f" You can reach them at {pharmacy_phone} to check if it's ready for pickup."
                return message, self.create_completion_node()

            elif status_lower == "pending doctor approval":
                message = f"Thank you, {caller_first_name}. I found your record. I see your refill request for {medication_name} is currently awaiting approval from {prescribing_physician}. The doctor's office typically reviews these within 1 to 2 business days."
                return message, self.create_completion_node()

            elif status_lower == "too early":
                message = f"Thank you, {caller_first_name}. I found your record. Based on your last fill date, it's a bit early for a refill of {medication_name}."
                if next_refill_date:
                    message += f" You'll be eligible for a refill on {next_refill_date}."
                message += " If you need an exception, I can connect you with our staff."
                return message, self.create_status_node()

            elif status_lower in ("ready for pickup", "ready"):
                message = f"Thank you, {caller_first_name}. I found your record. Your prescription for {medication_name} is ready for pickup at {pharmacy_name}."
                if pharmacy_phone:
                    message += f" You can reach them at {pharmacy_phone}."
                return message, self.create_completion_node()

            else:
                return f"Thank you, {caller_first_name}. I've verified your identity.", self.create_status_node()
        else:
            logger.warning(f"Flow: Identity verification failed - provided: {provided_name}, {provided_dob}")
            return None, self._create_verification_failed_node()

    # ==================== Handlers: Prescription Actions ====================

    async def _select_medication_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        medication_name = args.get("medication_name", "").strip()
        logger.info(f"Flow: Selected medication: {medication_name}")

        prescriptions = flow_manager.state.get("prescriptions", [])
        selected_rx = None
        for rx in prescriptions:
            if medication_name.lower() in rx.get("medication_name", "").lower():
                selected_rx = rx
                flow_manager.state["medication_name"] = rx.get("medication_name", "")
                flow_manager.state["dosage"] = rx.get("dosage", "")
                flow_manager.state["prescribing_physician"] = rx.get("prescribing_physician", "")
                flow_manager.state["refill_status"] = rx.get("refill_status", "")
                flow_manager.state["refills_remaining"] = rx.get("refills_remaining", 0)
                flow_manager.state["last_filled_date"] = rx.get("last_filled_date", "")
                flow_manager.state["next_refill_date"] = rx.get("next_refill_date", "")
                break

        if not selected_rx:
            return f"I couldn't find a prescription matching '{medication_name}'. Could you clarify which medication?", self.create_medication_identification_node()

        med_name = selected_rx.get("medication_name", medication_name)
        refill_status = selected_rx.get("refill_status", "")
        refills_remaining = selected_rx.get("refills_remaining", 0)
        pharmacy_name = flow_manager.state.get("pharmacy_name", "your pharmacy")
        pharmacy_phone = flow_manager.state.get("pharmacy_phone", "")

        status_lower = refill_status.lower() if refill_status else ""

        if status_lower == "active" and refills_remaining > 0:
            message = f"Your {med_name} prescription is active with {refills_remaining} refills remaining. Would you like me to send a refill to {pharmacy_name}?"
            return message, self.create_status_node()
        elif status_lower == "completed":
            message = f"Your {med_name} prescription has been completed. You would need a new prescription from your doctor for more."
            return message, self.create_completion_node()
        elif status_lower == "sent to pharmacy":
            message = f"Your {med_name} has already been sent to {pharmacy_name}."
            if pharmacy_phone:
                message += f" You can reach them at {pharmacy_phone} to check if it's ready."
            return message, self.create_completion_node()
        else:
            message = f"Your {med_name} prescription status is: {refill_status}."
            return message, self.create_status_node()

    async def _submit_refill_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        pharmacy_name = args.get("pharmacy_name", flow_manager.state.get("pharmacy_name", "your pharmacy"))
        medication_name = flow_manager.state.get("medication_name", "")

        logger.info(f"Flow: Submitting refill for {medication_name} to {pharmacy_name}")

        patient_id = self.patient_data.get("patient_id")
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

        patient_id = self.patient_data.get("patient_id")
        await self._try_db_update(
            patient_id, "update_patient",
            {"renewal_requested": True, "renewal_physician": physician, "call_status": "Completed"},
            error_msg="Error saving renewal request"
        )

        return f"I've submitted the refill request to {physician} for review. Once approved, the prescription will be sent to {pharmacy_name}. You should hear back within 1 to 2 business days.", self.create_completion_node()

    async def _check_another_prescription_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        logger.info("Flow: Checking another prescription")
        prescriptions = flow_manager.state.get("prescriptions", [])

        if len(prescriptions) > 1:
            return "", self.create_medication_identification_node()
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
            patient_data=self.patient_data, flow_manager=flow_manager, main_llm=self.main_llm,
            context_aggregator=self.context_aggregator, transport=self.transport, pipeline=self.pipeline,
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

        return None, target_flow.create_handoff_entry_node(context=reason)

    # ==================== Handlers: Transfers ====================

    async def _request_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        urgent = args.get("urgent", False)
        patient_confirmed = args.get("patient_confirmed", False)
        reason = args.get("reason", "general inquiry")

        logger.info(f"Flow: Staff transfer requested - reason: {reason}, urgent: {urgent}, confirmed: {patient_confirmed}")

        flow_manager.state["transfer_reason"] = reason
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

            patient_id = self.patient_data.get("patient_id")
            await self._try_db_update(patient_id, "update_patient", {"call_status": "Transferred"}, error_msg="Error updating call status")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception("Cold transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False
            return None, self.create_transfer_failed_node()

    # ==================== Handlers: End Call ====================

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Ending call")
        patient_id = self.patient_data.get("patient_id")

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            await self._try_db_update(patient_id, "update_call_status", "Completed", error_msg="Error updating call status")

        except Exception as e:
            logger.exception("Error in end_call_handler")
            await self._try_db_update(patient_id, "update_call_status", "Failed", error_msg="Failed to update status to Failed")

        return None, NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Thank the patient and say goodbye."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )
