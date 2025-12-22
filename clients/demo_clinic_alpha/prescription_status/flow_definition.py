import os
from typing import Dict, Any

from openai import AsyncOpenAI
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger

from backend.models import get_async_patient_db
from backend.utils import parse_natural_date
from handlers.transcript import save_transcript_to_db


async def warmup_openai(organization_name: str = "Demo Clinic Alpha"):
    """Warm up OpenAI with system prompt prefix for cache hits.

    OpenAI caches prompt prefixes of 1024+ tokens. We need to send a request
    with the same system prompt structure we use in actual calls to prime the cache.
    """
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Build a prompt that matches the structure used in actual calls
        # This needs to be 1024+ tokens for OpenAI to cache it
        global_instructions = f"""You are Jamie, a virtual assistant for {organization_name}, answering inbound calls from patients about their prescription refills.

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

        # Simulate the task messages structure to build up token count
        task_context = """# Goal
Verify patient identity by collecting name and date of birth. This step is important.

# Third-Party Caller Detection (CRITICAL)
If caller indicates they are NOT the patient, you MUST transfer to staff:
- "I'm calling for my mother/father/spouse/parent"
- "I'm calling on behalf of..."
- "I manage their medications"
- Any indication they are a family member, caregiver, or representative

Response: "For privacy reasons, I can only discuss prescription information directly with the patient or with documented authorization on file. Let me connect you with a staff member who can help."
→ Call request_staff immediately

# Verification Steps (only if caller IS the patient)
1. Ask: "May I have your full name?"
2. After name, ask: "And what is your date of birth?"

# When to Call Functions
- Name AND DOB match expected values → call verify_identity with verified=true
- Name OR DOB don't match → call verify_identity with verified=false"""

        # Add padding context to reach 1024 tokens (OpenAI's cache threshold)
        conversation_padding = """
# Prescription Status Scenarios

## Refills Available
You: "I can see you have a prescription for Amoxicillin, prescribed by Dr. Park. You have 2 refills remaining. Would you like me to send the refill to CVS Pharmacy?"
Patient: "Yes, please."
→ Call submit_refill with pharmacy_name="CVS Pharmacy"

## No Refills Remaining
You: "I see that this prescription has no refills remaining. To get more, Dr. Park will need to authorize a new prescription. Would you like me to submit that request?"
Patient: "Yes, please do that."
→ Call submit_renewal_request

## Pending Doctor Approval
You: "I see your refill request is currently awaiting approval from Dr. Park. The doctor's office typically reviews these within 1 to 2 business days."
Patient: "I'm almost out. Can you expedite this?"
→ "Let me connect you with a staff member who can help with that."
→ Call request_staff"""

        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": global_instructions},
                {"role": "system", "content": task_context},
                {"role": "system", "content": conversation_padding},
                {"role": "user", "content": "Hi, I'm calling about my prescription"},
                {"role": "assistant", "content": "I can help you with that. For your privacy and security, I need to verify your identity first. May I have your full name?"},
                {"role": "user", "content": "Robert Thompson"},
            ],
            max_tokens=1,
        )
        logger.info("OpenAI connection warmed up with prescription status prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class PrescriptionStatusFlow:
    """Prescription status inquiry flow for inbound patient calls.

    Flow:
    1. Greeting - Answer and identify as clinic
    2. Verification - Verify patient identity (name, DOB)
    3. Medication Identification - Determine which prescription they're asking about
    4. Status Communication - Share refill status, pharmacy info
    5. Closing - End call
    """

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

        # Initialize state from patient data
        self._init_state()

    def _init_state(self):
        """Initialize flow_manager state with patient data.

        Uses 'preserve if already set' pattern for identity fields to support
        cross-workflow handoffs where caller is already verified.
        """
        # Patient record (from clinic database) - preserve existing values for cross-workflow support
        self.flow_manager.state["patient_id"] = self.flow_manager.state.get("patient_id") or self.patient_data.get("patient_id")
        self.flow_manager.state["patient_name"] = self.flow_manager.state.get("patient_name") or self.patient_data.get("patient_name", "")
        self.flow_manager.state["first_name"] = self.flow_manager.state.get("first_name") or self.patient_data.get("first_name", "")
        self.flow_manager.state["last_name"] = self.flow_manager.state.get("last_name") or self.patient_data.get("last_name", "")
        self.flow_manager.state["date_of_birth"] = self.flow_manager.state.get("date_of_birth") or self.patient_data.get("date_of_birth", "")
        self.flow_manager.state["medical_record_number"] = self.flow_manager.state.get("medical_record_number") or self.patient_data.get("medical_record_number", "")
        self.flow_manager.state["phone_number"] = self.flow_manager.state.get("phone_number") or self.patient_data.get("phone_number", "")

        # Prescription information
        self.flow_manager.state["medication_name"] = self.patient_data.get("medication_name", "")
        self.flow_manager.state["dosage"] = self.patient_data.get("dosage", "")
        self.flow_manager.state["prescribing_physician"] = self.patient_data.get("prescribing_physician", "")
        self.flow_manager.state["refill_status"] = self.patient_data.get("refill_status", "")
        self.flow_manager.state["refills_remaining"] = self.patient_data.get("refills_remaining", 0)
        self.flow_manager.state["last_filled_date"] = self.patient_data.get("last_filled_date", "")
        self.flow_manager.state["next_refill_date"] = self.patient_data.get("next_refill_date", "")

        # Pharmacy information
        self.flow_manager.state["pharmacy_name"] = self.patient_data.get("pharmacy_name", "")
        self.flow_manager.state["pharmacy_phone"] = self.patient_data.get("pharmacy_phone", "")
        self.flow_manager.state["pharmacy_address"] = self.patient_data.get("pharmacy_address", "")

        # Multiple prescriptions (if patient has more than one)
        self.flow_manager.state["prescriptions"] = self.patient_data.get("prescriptions", [])

        # Verification state (preserve if already set from another workflow)
        self.flow_manager.state["identity_verified"] = self.flow_manager.state.get("identity_verified", False)

    def _get_full_name(self) -> str:
        """Get patient's full name from first_name and last_name."""
        first = self.flow_manager.state.get("first_name", "")
        last = self.flow_manager.state.get("last_name", "")
        if first and last:
            return f"{first} {last}"
        return first or last or ""

    def _normalize_name(self, name: str) -> str:
        """Normalize name for comparison (lowercase, handle 'Last, First' format)."""
        name = name.strip().lower()
        if "," in name:
            parts = [p.strip() for p in name.split(",")]
            if len(parts) == 2:
                return f"{parts[1]} {parts[0]}"
        return name

    def _normalize_dob(self, dob: str) -> str | None:
        """Normalize date of birth to ISO format for comparison."""
        if not dob:
            return None
        return parse_natural_date(dob.strip()) or dob.strip()

    def _get_global_instructions(self) -> str:
        """Global behavioral rules for prescription status inquiries."""
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

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting when patient calls."""
        greeting_text = f"Thank you for calling {self.organization_name}. This is Jamie. How can I help you today?"

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
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_verification",
                    description="""WHEN TO USE: Caller asks about prescription, refill, or medication.
RESULT: Transitions to identity verification before sharing any information.""",
                    properties={},
                    required=[],
                    handler=self._proceed_to_verification_handler,
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
        # Store context in state
        context_lower = context.lower()
        if "lisinopril" in context_lower:
            self.flow_manager.state["medication_name"] = "lisinopril"
        if "prior auth" in context_lower:
            self.flow_manager.state["issue_type"] = "prior_authorization"

        # Check if caller is already verified (handed off from another flow)
        if self.flow_manager.state.get("identity_verified"):
            first_name = self.flow_manager.state.get("first_name", "")
            logger.info(f"Flow: Caller already verified as {first_name}, skipping verification")
            # Go directly to status node
            return self.create_status_node()

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

The caller already explained their prescription issue. The previous assistant acknowledged it.
IMMEDIATELY call proceed_to_verification (do NOT speak first - no greeting, no acknowledgment).

The context shows: {context}
Note any medication names or complications for later.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_verification",
                    description="Proceed immediately to identity verification.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_verification_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_verification_node(self) -> NodeConfig:
        """Verify patient identity with name and DOB."""
        state = self.flow_manager.state
        # Build full name for comparison
        first_name = state.get("first_name", "")
        last_name = state.get("last_name", "")
        stored_name = f"{first_name} {last_name}".strip() if first_name or last_name else state.get("patient_name", "")
        stored_dob = state.get("date_of_birth", "")

        return NodeConfig(
            name="verification",
            task_messages=[
                {
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
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="verify_identity",
                    description="""WHEN TO USE: After collecting BOTH name AND date of birth from caller.
RESULT: Verifies against stored record and proceeds to prescription status if matched.

EXAMPLES:
- Caller says "Jennifer Martinez" and "September 12, 1980" → call with those values
- Caller says "John Doe, born January 5, 1990" → call with name="John Doe", date_of_birth="January 5, 1990" """,
                    properties={
                        "name": {
                            "type": "string",
                            "description": "Caller's full name as stated (first and last)",
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Caller's date of birth in natural format (e.g., 'September 12, 1980')",
                        },
                    },
                    required=["name", "date_of_birth"],
                    handler=self._verify_identity_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=False,
        )

    def create_medication_identification_node(self) -> NodeConfig:
        """Identify which medication the patient is asking about."""
        state = self.flow_manager.state
        first_name = state.get("first_name", "there")
        prescriptions = state.get("prescriptions", [])

        # Build prescription list for prompt
        if len(prescriptions) > 1:
            rx_list = "\n".join([f"- {rx.get('medication_name', 'Unknown')} ({rx.get('dosage', '')})" for rx in prescriptions])
            multi_rx_context = f"""# Multiple Prescriptions on File
{rx_list}

Ask which medication they're calling about. If they describe it vaguely, help identify it:
"I see you have Amoxicillin and Chlorhexidine on file. Which one are you calling about?"
"""
        else:
            multi_rx_context = ""

        return NodeConfig(
            name="medication_identification",
            task_messages=[
                {
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
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="select_medication",
                    description="""Select the medication patient is asking about.

WHEN TO USE: After patient confirms which prescription they need.
VALID VALUES: Medication name from their prescription list.""",
                    properties={
                        "medication_name": {
                            "type": "string",
                            "description": "Name of the medication selected",
                        },
                    },
                    required=["medication_name"],
                    handler=self._select_medication_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call when caller says goodbye.
WHEN TO USE: Caller says goodbye or indicates they're done.""",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_status_node(self) -> NodeConfig:
        """Communicate prescription status to patient."""
        state = self.flow_manager.state
        first_name = state.get("first_name", "there")

        # Get prescription details
        medication_name = state.get("medication_name", "Unknown")
        dosage = state.get("dosage", "")
        prescribing_physician = state.get("prescribing_physician", "your doctor")
        refill_status = state.get("refill_status", "Unknown")
        refills_remaining = state.get("refills_remaining", 0)
        last_filled_date = state.get("last_filled_date", "Unknown")
        next_refill_date = state.get("next_refill_date", "")

        # Pharmacy info
        pharmacy_name = state.get("pharmacy_name", "your pharmacy")
        pharmacy_phone = state.get("pharmacy_phone", "")
        pharmacy_address = state.get("pharmacy_address", "")

        return NodeConfig(
            name="status",
            task_messages=[
                {
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

# Example Flows

## Already Sent to Pharmacy
You: "Your refill for {medication_name} has already been sent to {pharmacy_name}. You can reach them at {pharmacy_phone} to check if it's ready for pickup."
Patient: "Great, thanks!"
→ Call end_call or ask if there's anything else

## Pending Doctor Approval
You: "I see your refill request for {medication_name} is currently awaiting approval from {prescribing_physician}. The doctor's office typically reviews these within 1 to 2 business days."
Patient: "I'm almost out. Can you expedite this?"
→ "Let me connect you with a staff member who can help with that."
→ Call request_staff

## Refills Available
You: "I can see you have a prescription for {medication_name}, prescribed by {prescribing_physician}. You have {refills_remaining} refills remaining. Would you like me to send the refill to {pharmacy_name}?"
Patient: "Yes, please."
→ Call submit_refill

## No Refills
You: "I see that this prescription has no refills remaining. The last refill was on {last_filled_date}. To get more, {prescribing_physician} will need to authorize a new prescription. Would you like me to submit that request?"
Patient: "Yes, please do that."
→ Call submit_renewal_request

## Pharmacy Change Request
Patient: "Can you send it to a different pharmacy?"
You: "I'll need to connect you with a staff member who can update your pharmacy on file."
→ Call request_staff

# Guardrails
- Never provide medical advice about medications
- Record information immediately via function calls. This step is important.
- If patient asks about dosage changes or medical concerns, recommend speaking with their doctor

# Error Handling
If you don't understand the patient's request:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never guess what action to take—always confirm
- If function call fails, continue naturally without mentioning technical issues""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="submit_refill",
                    description="""Submit refill to pharmacy when patient confirms.

WHEN TO USE: Patient has refills available AND confirms they want the refill sent.
RESULT: Refill request sent to pharmacy on file.""",
                    properties={
                        "pharmacy_name": {
                            "type": "string",
                            "description": "Pharmacy name (use current if not changed)",
                        },
                    },
                    required=["pharmacy_name"],
                    handler=self._submit_refill_handler,
                ),
                FlowsFunctionSchema(
                    name="submit_renewal_request",
                    description="""Submit renewal request to prescribing physician.

WHEN TO USE: No refills remaining AND patient wants a renewal.
RESULT: Request sent to doctor for review (1-2 business days).""",
                    properties={},
                    required=[],
                    handler=self._submit_renewal_request_handler,
                ),
                FlowsFunctionSchema(
                    name="check_another_prescription",
                    description="""Patient wants to check status of another medication.

WHEN TO USE:
- Patient asks about a different prescription after resolving the first
- Patient mentions another medication they want to check

RESULT: Returns to medication identification node if multiple prescriptions exist.""",
                    properties={},
                    required=[],
                    handler=self._check_another_prescription_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call gracefully.

WHEN TO USE:
- Patient confirms they have no more questions
- Patient indicates they're done ("that's all", "thank you, goodbye")

RESULT: Transition to closing node to thank patient and end call.""",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_closing_node(self) -> NodeConfig:
        """Thank patient and end call."""
        state = self.flow_manager.state
        first_name = state.get("first_name", "")

        return NodeConfig(
            name="closing",
            task_messages=[
                {
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
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="route_to_workflow",
                    description="""Route to scheduling or lab results workflow.

WHEN TO USE:
- Caller asks to SCHEDULE an APPOINTMENT → workflow="scheduling"
- Caller asks about LAB RESULTS or test results → workflow="lab_results"

SCHEDULING EXAMPLES (use workflow="scheduling"):
- "I need to schedule a follow-up appointment" → scheduling
- "Can I book an appointment with Dr. Williams?" → scheduling
- "I want to schedule a visit" → scheduling

LAB RESULTS EXAMPLES (use workflow="lab_results"):
- "Are my blood test results back?" → lab_results

IMPORTANT: Use this for scheduling, NOT request_staff.""",
                    properties={
                        "workflow": {
                            "type": "string",
                            "enum": ["lab_results", "scheduling"],
                            "description": "Workflow: scheduling (appointments) or lab_results (test results)",
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
                    name="check_another_prescription",
                    description="""Patient wants to check status of another medication/prescription.

WHEN TO USE:
- Patient asks about ANOTHER medication, prescription, refill, or medicine
- Patient mentions a different drug name
- ANY prescription/medication question

EXAMPLES:
- "What about my fluoride rinse?" → call this
- "Can you check my other prescription?" → call this
- "I also need my blood pressure medication" → call this

RESULT: Returns to medication identification node.""",
                    properties={},
                    required=[],
                    handler=self._check_another_prescription_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call gracefully.

WHEN TO USE:
- Patient confirms NO more questions
- Patient says goodbye or thanks

RESULT: Call ends with goodbye message.""",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=False,
        )

    def _create_end_node(self) -> NodeConfig:
        """Terminal node that ends the conversation."""
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

    def create_post_scheduling_node(self, scheduling_flow, transition_message: str = "") -> NodeConfig:
        self._scheduling_flow = scheduling_flow
        return NodeConfig(
            name="post_scheduling",
            task_messages=[{"role": "system", "content": "Call proceed_to_scheduling immediately."}],
            functions=[FlowsFunctionSchema(
                name="proceed_to_scheduling", description="Proceed to scheduling.", properties={}, required=[],
                handler=self._proceed_to_scheduling_handler,
            )],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": transition_message}] if transition_message else None,
        )

    async def _proceed_to_scheduling_handler(self, args: dict, flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        return None, self._scheduling_flow.create_scheduling_node()

    def create_post_lab_results_node(self, lab_results_flow, transition_message: str = "") -> NodeConfig:
        self._lab_results_flow = lab_results_flow
        return NodeConfig(
            name="post_lab_results",
            task_messages=[{"role": "system", "content": "Call proceed_to_lab_results immediately."}],
            functions=[FlowsFunctionSchema(
                name="proceed_to_lab_results", description="Proceed to lab results.", properties={}, required=[],
                handler=self._proceed_to_lab_results_handler,
            )],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": transition_message}] if transition_message else None,
        )

    async def _proceed_to_lab_results_handler(self, args: dict, flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        return None, self._lab_results_flow.create_results_node()

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
        # Get transfer reason for context
        transfer_reason = self.flow_manager.state.get("transfer_reason", "")

        # Build alternative suggestions based on reason
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
            role_messages=[
                {
                    "role": "system",
                    "content": self._get_global_instructions(),
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": f"""The transfer didn't go through. Apologize and offer alternatives.

{alternatives}

If caller accepts an alternative (note on file, callback, etc.):
→ Acknowledge and call end_call

If caller says goodbye or wants to end call:
→ Call end_call

If caller has a question you can answer:
→ Answer it, then ask if there's anything else""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call gracefully.

WHEN TO USE: Caller says goodbye, confirms no more questions, or accepts an alternative.
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

    def _create_verification_failed_node(self) -> NodeConfig:
        """Node when identity verification fails."""
        return NodeConfig(
            name="verification_failed",
            task_messages=[],
            functions=[],
            pre_actions=[
                {"type": "tts_say", "text": "I'm sorry, I wasn't able to verify your identity. For your security, I'll need to transfer you to a staff member who can assist you. One moment please."}
            ],
            post_actions=[{"type": "end_conversation"}],
        )

    # ========== Function Handlers ==========

    def _get_request_staff_function(self) -> FlowsFunctionSchema:
        """Return the request_staff function schema for use in multiple nodes."""
        return FlowsFunctionSchema(
            name="request_staff",
            description="""Transfer call to human staff member.

WHEN TO USE:
- Caller is a third-party (family member, caregiver) calling on behalf of patient
- Patient refuses to provide verification information
- Patient needs help with pharmacy change
- Patient requests to expedite a pending prescription
- Patient has medical concerns or dosage questions
- Patient explicitly requests to speak with a human

EXAMPLES:
- Third-party caller → call with reason="third_party"
- Patient says "I want to talk to a person" → call with patient_confirmed=true
- Urgent medical concern → call with urgent=true""",
            properties={
                "urgent": {
                    "type": "boolean",
                    "description": "Set true for urgent requests that need immediate attention (medical concerns, frustrated caller). Transfers immediately.",
                },
                "patient_confirmed": {
                    "type": "boolean",
                    "description": "Set true if patient explicitly asked for human/staff transfer. Transfers immediately.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason for transfer (e.g., 'third_party', 'pharmacy_change', 'expedite', 'medical_question')",
                },
            },
            required=[],
            handler=self._request_staff_handler,
        )

    async def _proceed_to_verification_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Transition to verification node."""
        logger.info("Flow: Proceeding to verification")
        # Handler return is GUARANTEED to be spoken - ask for name first (step 1)
        return "For privacy and security, I need to verify your identity first. May I have your first and last name?", self.create_verification_node()

    async def _verify_identity_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Handle identity verification result."""
        provided_name = args.get("name", "").strip()
        provided_dob = args.get("date_of_birth", "").strip()

        # Get stored patient info for comparison
        first_name = flow_manager.state.get("first_name", "")
        last_name = flow_manager.state.get("last_name", "")
        stored_name = f"{first_name} {last_name}".strip() if first_name or last_name else flow_manager.state.get("patient_name", "")
        stored_dob = flow_manager.state.get("date_of_birth", "")

        # Normalize for comparison
        provided_name_normalized = self._normalize_name(provided_name)
        stored_name_normalized = self._normalize_name(stored_name)
        provided_dob_normalized = self._normalize_dob(provided_dob)
        stored_dob_normalized = self._normalize_dob(stored_dob)

        # Check both name AND DOB match
        name_match = provided_name_normalized == stored_name_normalized if stored_name_normalized else False
        dob_match = provided_dob_normalized == stored_dob_normalized if stored_dob_normalized else False

        logger.info(f"Flow: Identity verification - name_match={name_match}, dob_match={dob_match}")

        # Extract first name for personalization
        caller_first_name = provided_name.strip().split()[0].title() if provided_name.strip() else "there"

        if name_match and dob_match:
            flow_manager.state["identity_verified"] = True

            # Parse and store first_name/last_name for cross-workflow compatibility
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

            # Check if patient has multiple prescriptions
            prescriptions = flow_manager.state.get("prescriptions", [])
            if len(prescriptions) > 1:
                return f"Thank you, {caller_first_name}. I've verified your identity. Which medication are you calling about today?", self.create_medication_identification_node()

            # Single prescription - share status directly
            # Handler return is GUARANTEED to be spoken, so we use it for critical info
            medication_name = flow_manager.state.get("medication_name", "your medication")
            refill_status = flow_manager.state.get("refill_status", "")
            pharmacy_name = flow_manager.state.get("pharmacy_name", "your pharmacy")
            pharmacy_phone = flow_manager.state.get("pharmacy_phone", "")
            prescribing_physician = flow_manager.state.get("prescribing_physician", "your doctor")
            next_refill_date = flow_manager.state.get("next_refill_date", "")

            # Build status message based on refill_status
            status_lower = refill_status.lower() if refill_status else ""

            if status_lower == "sent to pharmacy":
                message = f"Thank you, {caller_first_name}. I found your record. Your refill for {medication_name} has already been sent to {pharmacy_name}."
                if pharmacy_phone:
                    message += f" You can reach them at {pharmacy_phone} to check if it's ready for pickup."
                message += " Is there anything else I can help you with?"
                return message, self.create_closing_node()

            elif status_lower == "pending doctor approval":
                message = f"Thank you, {caller_first_name}. I found your record. I see your refill request for {medication_name} is currently awaiting approval from {prescribing_physician}. The doctor's office typically reviews these within 1 to 2 business days. Is there anything else I can help you with?"
                return message, self.create_closing_node()

            elif status_lower == "too early":
                message = f"Thank you, {caller_first_name}. I found your record. Based on your last fill date, it's a bit early for a refill of {medication_name}."
                if next_refill_date:
                    message += f" You'll be eligible for a refill on {next_refill_date}."
                message += " If you need an exception, I can connect you with our staff. Is there anything else I can help you with?"
                # Go to status node so caller can request exception and transfer to staff
                return message, self.create_status_node()

            elif status_lower in ("ready for pickup", "ready"):
                message = f"Thank you, {caller_first_name}. I found your record. Your prescription for {medication_name} is ready for pickup at {pharmacy_name}."
                if pharmacy_phone:
                    message += f" You can reach them at {pharmacy_phone}."
                message += " Is there anything else I can help you with?"
                return message, self.create_closing_node()

            else:
                # Active/other status - go to status node for LLM to handle refill/renewal offers
                return f"Thank you, {caller_first_name}. I've verified your identity.", self.create_status_node()
        else:
            # Verification failed
            logger.warning(f"Flow: Identity verification failed - provided: {provided_name}, {provided_dob}")
            return None, self._create_verification_failed_node()

    async def _select_medication_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Handle medication selection and share status directly."""
        medication_name = args.get("medication_name", "").strip()
        logger.info(f"Flow: Selected medication: {medication_name}")

        # Find the prescription in the list and populate state
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

        # Share status directly (handler message is guaranteed to be spoken)
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
            return message, self.create_closing_node()
        elif status_lower == "sent to pharmacy":
            message = f"Your {med_name} has already been sent to {pharmacy_name}."
            if pharmacy_phone:
                message += f" You can reach them at {pharmacy_phone} to check if it's ready."
            return message, self.create_closing_node()
        else:
            message = f"Your {med_name} prescription status is: {refill_status}."
            return message, self.create_status_node()

    async def _submit_refill_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Submit refill request to pharmacy."""
        pharmacy_name = args.get("pharmacy_name", flow_manager.state.get("pharmacy_name", "your pharmacy"))
        medication_name = flow_manager.state.get("medication_name", "")

        logger.info(f"Flow: Submitting refill for {medication_name} to {pharmacy_name}")

        # Update database
        try:
            patient_id = self.patient_data.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                await db.update_patient(
                    patient_id,
                    {
                        "refill_requested": True,
                        "refill_pharmacy": pharmacy_name,
                        "call_status": "Completed",
                    },
                    self.organization_id,
                )
                logger.info(f"Refill request saved to database: {patient_id}")
        except Exception as e:
            logger.error(f"Error saving refill request: {e}")

        return f"I've submitted the refill request to {pharmacy_name}. They should have it ready within 2 to 4 hours. Is there anything else I can help you with?", self.create_closing_node()

    async def _submit_renewal_request_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Submit renewal request to prescribing physician."""
        physician = flow_manager.state.get("prescribing_physician", "your doctor")
        medication_name = flow_manager.state.get("medication_name", "")
        pharmacy_name = flow_manager.state.get("pharmacy_name", "your pharmacy")

        logger.info(f"Flow: Submitting renewal request for {medication_name} to {physician}")

        # Update database
        try:
            patient_id = self.patient_data.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                await db.update_patient(
                    patient_id,
                    {
                        "renewal_requested": True,
                        "renewal_physician": physician,
                        "call_status": "Completed",
                    },
                    self.organization_id,
                )
                logger.info(f"Renewal request saved to database: {patient_id}")
        except Exception as e:
            logger.error(f"Error saving renewal request: {e}")

        return f"I've submitted the refill request to {physician} for review. Once approved, the prescription will be sent to {pharmacy_name}. You should hear back within 1 to 2 business days. Is there anything else I can help you with?", self.create_closing_node()

    async def _check_another_prescription_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Patient wants to check another prescription."""
        logger.info("Flow: Checking another prescription")
        prescriptions = flow_manager.state.get("prescriptions", [])

        if len(prescriptions) > 1:
            # Return empty message - LLM will ask which medication or caller already specified
            # This prevents duplicate responses when select_medication is called in same turn
            return "", self.create_medication_identification_node()
        else:
            return "I only see one prescription on file for you. Is there something else I can help you with?", self.create_closing_node()

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """End the call."""
        logger.info("Flow: Ending call")
        patient_id = self.patient_data.get("patient_id")
        db = get_async_patient_db() if patient_id else None

        try:
            # Save transcript
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            # Update call status
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
        """Transfer to staff member via cold transfer."""
        urgent = args.get("urgent", False)
        patient_confirmed = args.get("patient_confirmed", False)
        reason = args.get("reason", "general inquiry")

        logger.info(f"Flow: Staff transfer requested - reason: {reason}, urgent: {urgent}, confirmed: {patient_confirmed}")

        # Store reason for potential retry
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

            # Update call status
            try:
                patient_id = self.patient_data.get("patient_id")
                if patient_id:
                    db = get_async_patient_db()
                    await db.update_patient(
                        patient_id,
                        {"call_status": "Transferred"},
                        self.organization_id,
                    )
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

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception("Cold transfer retry failed")

            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            return None, self.create_transfer_failed_node()

    async def _return_to_conversation_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Return to the previous conversation node after failed transfer."""
        logger.info("Flow: Returning to status node after failed transfer")
        return "No problem, let me continue helping you.", self.create_status_node()

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
        elif workflow == "scheduling":
            return await self._handoff_to_scheduling(flow_manager, reason)
        else:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

    async def _handoff_to_lab_results(
        self, flow_manager: FlowManager, reason: str
    ) -> tuple[None, NodeConfig]:
        from clients.demo_clinic_alpha.lab_results.flow_definition import LabResultsFlow

        lab_results_flow = LabResultsFlow(
            patient_data=self.patient_data, flow_manager=flow_manager, main_llm=self.main_llm,
            context_aggregator=self.context_aggregator, transport=self.transport, pipeline=self.pipeline,
            organization_id=self.organization_id, cold_transfer_config=self.cold_transfer_config,
        )
        logger.info(f"Flow: Handing off to LabResultsFlow - {reason}")

        if flow_manager.state.get("identity_verified"):
            first_name = flow_manager.state.get("first_name", "")
            msg = f"Let me check on that for you, {first_name}." if first_name else "Let me check on that for you."
            return None, self.create_post_lab_results_node(lab_results_flow, msg)
        return None, lab_results_flow.create_handoff_entry_node(context=reason)

    async def _handoff_to_scheduling(
        self, flow_manager: FlowManager, reason: str
    ) -> tuple[None, NodeConfig]:
        from clients.demo_clinic_alpha.patient_scheduling.flow_definition import PatientSchedulingFlow

        scheduling_flow = PatientSchedulingFlow(
            patient_data=self.patient_data, flow_manager=flow_manager, main_llm=self.main_llm,
            context_aggregator=self.context_aggregator, transport=self.transport, pipeline=self.pipeline,
            organization_id=self.organization_id, cold_transfer_config=self.cold_transfer_config,
        )
        logger.info(f"Flow: Handing off to PatientSchedulingFlow - {reason}")

        if flow_manager.state.get("identity_verified"):
            first_name = flow_manager.state.get("first_name", "")
            flow_manager.state["appointment_reason"] = reason
            flow_manager.state["appointment_type"] = "Returning Patient"
            msg = f"I can help with that, {first_name}!" if first_name else "I can help with that!"
            return None, self.create_post_scheduling_node(scheduling_flow, msg)
        return None, scheduling_flow.create_handoff_entry_node(context=reason)
