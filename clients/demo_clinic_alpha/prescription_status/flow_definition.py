from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger
from backend.models import get_async_patient_db


class PrescriptionStatusFlow:
    """Prescription status inquiry flow for inbound patient calls.

    Flow:
    1. Greeting - Answer and identify as clinic
    2. Verification - Verify patient identity (name, DOB)
    3. Medication Identification - Determine which prescription they're asking about
    4. Status Communication - Share refill status, pharmacy info
    5. Closing - End call
    """

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager = None,
                 main_llm=None, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None):
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self._patient_data = patient_data
        self.organization_name = patient_data.get("organization_name", "Demo Clinic Alpha")

    def _init_flow_state(self):
        """Initialize flow_manager state with patient data. Called after flow_manager is set."""
        if not self.flow_manager:
            return

        # Patient record (from clinic database)
        self.flow_manager.state["patient_id"] = self._patient_data.get("patient_id")
        self.flow_manager.state["patient_name"] = self._patient_data.get("patient_name", "")
        self.flow_manager.state["date_of_birth"] = self._patient_data.get("date_of_birth", "")
        self.flow_manager.state["medical_record_number"] = self._patient_data.get("medical_record_number", "")
        self.flow_manager.state["phone_number"] = self._patient_data.get("phone_number", "")

        # Prescription information
        self.flow_manager.state["medication_name"] = self._patient_data.get("medication_name", "")
        self.flow_manager.state["dosage"] = self._patient_data.get("dosage", "")
        self.flow_manager.state["prescribing_physician"] = self._patient_data.get("prescribing_physician", "")
        self.flow_manager.state["refill_status"] = self._patient_data.get("refill_status", "")
        self.flow_manager.state["refills_remaining"] = self._patient_data.get("refills_remaining", 0)
        self.flow_manager.state["last_filled_date"] = self._patient_data.get("last_filled_date", "")
        self.flow_manager.state["next_refill_date"] = self._patient_data.get("next_refill_date", "")

        # Pharmacy information
        self.flow_manager.state["pharmacy_name"] = self._patient_data.get("pharmacy_name", "")
        self.flow_manager.state["pharmacy_phone"] = self._patient_data.get("pharmacy_phone", "")
        self.flow_manager.state["pharmacy_address"] = self._patient_data.get("pharmacy_address", "")

        # Multiple prescriptions (if patient has more than one)
        self.flow_manager.state["prescriptions"] = self._patient_data.get("prescriptions", [])

        # Verification state
        self.flow_manager.state["identity_verified"] = False

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
Determine what the caller needs and route appropriately.

# Expected Responses
If caller mentions prescription, refill, or medication:
→ Call start_verification to verify their identity

If caller needs something else (appointments, billing, medical questions):
→ Say "Let me connect you with someone who can help with that." and call request_staff

# Example Flow
Caller: "Hi, I need to check on a prescription refill."
→ "I can help you with that. For your privacy and security, I need to verify your identity first."
→ Call start_verification""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="start_verification",
                    description="Call when patient wants to check prescription status. Initiates identity verification.",
                    properties={},
                    required=[],
                    handler=self._start_verification_handler,
                ),
                FlowsFunctionSchema(
                    name="request_staff",
                    description="Transfer to staff for non-prescription inquiries.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler,
                ),
            ],
            respond_immediately=False,
            pre_actions=[
                {"type": "tts_say", "text": greeting_text},
            ],
        )

    def create_verification_node(self) -> NodeConfig:
        """Verify patient identity with name and DOB."""
        state = self.flow_manager.state
        stored_name = state.get("patient_name", "")
        stored_dob = state.get("date_of_birth", "")
        stored_phone = state.get("phone_number", "")

        return NodeConfig(
            name="verification",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""# Goal
Verify patient identity by collecting name and date of birth. This step is important.

# Patient Record (for verification)
- Expected Name: {stored_name}
- Expected DOB: {stored_dob}
- Phone ending in: {stored_phone[-4:] if len(stored_phone) >= 4 else "Unknown"}

# Verification Steps
1. Ask: "May I have your full name?"
2. After name, ask: "And what is your date of birth?"
3. Optionally confirm phone: "Just to confirm, is this the phone number ending in XXXX?"

# When to Call Functions
- Name AND DOB match expected values → call verify_identity with verified=true
- Name OR DOB don't match → call verify_identity with verified=false
- Patient refuses to provide info → call request_staff

# Data Normalization
Dates spoken naturally should be understood:
- "July 8th, 1965" or "7/8/65" → compare against stored DOB
- Names may have slight variations—use judgment for obvious matches

# Example Flow
You: "May I have your full name?"
Patient: "Robert Thompson"
You: "Thank you, Robert. And what is your date of birth?"
Patient: "July 8th, 1965"
→ Call verify_identity with name="Robert Thompson", date_of_birth="July 8, 1965", verified=true

# Guardrails
- Never reveal stored information to unverified callers
- If verification fails, do NOT provide any prescription details""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="verify_identity",
                    description="""Verify patient identity after collecting name and DOB.

WHEN TO USE: After patient provides both name AND date of birth.
HOW TO VERIFY: Compare provided info against expected values.

Call with verified=true if name and DOB match.
Call with verified=false if they don't match.""",
                    properties={
                        "name": {
                            "type": "string",
                            "description": "Patient's full name as provided",
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Patient's date of birth as provided",
                        },
                        "verified": {
                            "type": "boolean",
                            "description": "True if name and DOB match expected values, false otherwise",
                        },
                    },
                    required=["name", "date_of_birth", "verified"],
                    handler=self._verify_identity_handler,
                ),
                FlowsFunctionSchema(
                    name="request_staff",
                    description="Transfer to staff if patient refuses verification or requests human.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_medication_identification_node(self) -> NodeConfig:
        """Identify which medication the patient is asking about."""
        state = self.flow_manager.state
        first_name = state.get("patient_name", "").split(",")[1].strip() if "," in state.get("patient_name", "") else state.get("patient_name", "").split()[0] if state.get("patient_name") else "there"
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
Determine which prescription the patient is asking about.

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
→ Call select_medication with medication_name="Amoxicillin" """,
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
                    name="request_staff",
                    description="Transfer to staff if unable to identify medication.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_status_node(self) -> NodeConfig:
        """Communicate prescription status to patient."""
        state = self.flow_manager.state
        first_name = state.get("verified_first_name", "there")

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
                    "content": f"""# Goal
Share prescription status and handle refill requests appropriately. This step is important.

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

## Refills Available (refills_remaining > 0, status is Active/Ready)
1. Confirm medication details
2. Offer to send refill: "You have {refills_remaining} refills remaining. Would you like me to send the refill to your pharmacy?"
3. If yes → confirm pharmacy or update if requested → call submit_refill
4. Inform: "Your pharmacy should have it ready within 2 to 4 hours."

## No Refills Remaining (refills_remaining = 0)
1. Explain: "This prescription has no refills remaining."
2. Offer to request renewal: "I can submit a refill request to {prescribing_physician} for review. This typically takes 1 to 2 business days."
3. If yes → call submit_renewal_request

## Too Early to Refill (status is Too Early)
1. Explain: "Based on your last fill date, it's a bit early for a refill."
2. Provide next eligible date: "You'll be eligible for a refill on {next_refill_date}."
3. If patient insists they're running low, offer to note it for the doctor

## Prescription Expired or Completed
1. Explain the prescription is no longer active
2. Recommend scheduling follow-up if needed

# Pharmacy Update
If patient wants to change pharmacy:
1. Ask for new pharmacy name and phone number
2. Call update_pharmacy before submitting refill

# Example Flows

## Refills Available
You: "I can see you have a prescription for {medication_name}, prescribed by {prescribing_physician}. You have {refills_remaining} refills remaining. Would you like me to send the refill to {pharmacy_name}?"
Patient: "Yes, please."
→ Call submit_refill

## No Refills
You: "I see that this prescription has no refills remaining. The last refill was on {last_filled_date}. To get more, {prescribing_physician} will need to authorize a new prescription. Would you like me to submit that request?"
Patient: "Yes, please do that."
→ Call submit_renewal_request

# Guardrails
- Never provide medical advice about medications
- Record information immediately via function calls. This step is important.
- If patient asks about dosage changes or medical concerns, recommend speaking with their doctor""",
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
                    name="update_pharmacy",
                    description="""Update patient's pharmacy preference.

WHEN TO USE: Patient requests a different pharmacy before refill.
REQUIRED: Get pharmacy name and phone number from patient.""",
                    properties={
                        "pharmacy_name": {
                            "type": "string",
                            "description": "New pharmacy name",
                        },
                        "pharmacy_phone": {
                            "type": "string",
                            "description": "New pharmacy phone number (digits only)",
                        },
                        "pharmacy_address": {
                            "type": "string",
                            "description": "New pharmacy address if provided",
                        },
                    },
                    required=["pharmacy_name", "pharmacy_phone"],
                    handler=self._update_pharmacy_handler,
                ),
                FlowsFunctionSchema(
                    name="check_another_prescription",
                    description="Patient wants to check on a different prescription.",
                    properties={},
                    required=[],
                    handler=self._check_another_prescription_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="Patient has no more questions and wants to end the call.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                FlowsFunctionSchema(
                    name="request_staff",
                    description="Transfer to staff for complex issues or patient request.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler,
                ),
            ],
            respond_immediately=True,
        )

    def create_closing_node(self) -> NodeConfig:
        """Thank patient and end call."""
        state = self.flow_manager.state
        first_name = state.get("verified_first_name", "")

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
3. If yes → handle the request or transfer to staff

# Example
You: "Is there anything else I can help you with today?"
Patient: "No, that's everything. Thank you."
You: "You're welcome{', ' + first_name if first_name else ''}. Thank you for calling {self.organization_name}. Have a great day."
→ Call end_call""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the call after patient confirms they're done.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                FlowsFunctionSchema(
                    name="check_another_prescription",
                    description="Patient wants to check on another prescription.",
                    properties={},
                    required=[],
                    handler=self._check_another_prescription_handler,
                ),
                FlowsFunctionSchema(
                    name="request_staff",
                    description="Transfer to staff for additional requests.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler,
                ),
            ],
            respond_immediately=True,
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

    def _create_transfer_node(self) -> NodeConfig:
        """Node for transferring to staff."""
        return NodeConfig(
            name="transfer",
            task_messages=[],
            functions=[],
            pre_actions=[
                {"type": "tts_say", "text": "Let me connect you with a colleague who can help. One moment please."}
            ],
            post_actions=[{"type": "end_conversation"}],
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

    async def _start_verification_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Transition to verification node."""
        logger.info("Flow: Starting identity verification")
        return "May I have your full name?", self.create_verification_node()

    async def _verify_identity_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Handle identity verification result."""
        name = args.get("name", "").strip()
        dob = args.get("date_of_birth", "").strip()
        verified = args.get("verified", False)

        if verified:
            flow_manager.state["identity_verified"] = True
            # Extract first name for personalization
            first_name = name.split()[0] if name else ""
            flow_manager.state["verified_first_name"] = first_name

            logger.info(f"Flow: Identity verified for {first_name}")

            # Check if patient has multiple prescriptions
            prescriptions = flow_manager.state.get("prescriptions", [])
            if len(prescriptions) > 1:
                return f"Thank you, {first_name}. I've verified your identity. Which medication are you calling about today?", self.create_medication_identification_node()
            else:
                # Single prescription - go directly to status
                return f"Thank you, {first_name}. I've verified your identity.", self.create_status_node()
        else:
            logger.warning(f"Flow: Identity verification failed for name={name}")
            return None, self._create_verification_failed_node()

    async def _select_medication_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Handle medication selection."""
        medication_name = args.get("medication_name", "").strip()
        logger.info(f"Flow: Selected medication: {medication_name}")

        # Find the prescription in the list and populate state
        prescriptions = flow_manager.state.get("prescriptions", [])
        for rx in prescriptions:
            if medication_name.lower() in rx.get("medication_name", "").lower():
                flow_manager.state["medication_name"] = rx.get("medication_name", "")
                flow_manager.state["dosage"] = rx.get("dosage", "")
                flow_manager.state["prescribing_physician"] = rx.get("prescribing_physician", "")
                flow_manager.state["refill_status"] = rx.get("refill_status", "")
                flow_manager.state["refills_remaining"] = rx.get("refills_remaining", 0)
                flow_manager.state["last_filled_date"] = rx.get("last_filled_date", "")
                flow_manager.state["next_refill_date"] = rx.get("next_refill_date", "")
                break

        return "Let me look that up for you.", self.create_status_node()

    async def _submit_refill_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Submit refill request to pharmacy."""
        pharmacy_name = args.get("pharmacy_name", flow_manager.state.get("pharmacy_name", "your pharmacy"))
        medication_name = flow_manager.state.get("medication_name", "")

        logger.info(f"Flow: Submitting refill for {medication_name} to {pharmacy_name}")

        # Update database
        try:
            patient_id = self._patient_data.get("patient_id")
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
            patient_id = self._patient_data.get("patient_id")
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

    async def _update_pharmacy_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Update patient's pharmacy preference."""
        new_pharmacy_name = args.get("pharmacy_name", "").strip()
        new_pharmacy_phone = args.get("pharmacy_phone", "").strip()
        new_pharmacy_address = args.get("pharmacy_address", "").strip()

        # Normalize phone to digits only
        phone_digits = ''.join(c for c in new_pharmacy_phone if c.isdigit())

        # Update state
        flow_manager.state["pharmacy_name"] = new_pharmacy_name
        flow_manager.state["pharmacy_phone"] = phone_digits
        if new_pharmacy_address:
            flow_manager.state["pharmacy_address"] = new_pharmacy_address

        logger.info(f"Flow: Updated pharmacy to {new_pharmacy_name}")

        # Update database
        try:
            patient_id = self._patient_data.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                update_fields = {
                    "pharmacy_name": new_pharmacy_name,
                    "pharmacy_phone": phone_digits,
                }
                if new_pharmacy_address:
                    update_fields["pharmacy_address"] = new_pharmacy_address
                await db.update_patient(patient_id, update_fields, self.organization_id)
                logger.info(f"Pharmacy updated in database: {patient_id}")
        except Exception as e:
            logger.error(f"Error updating pharmacy: {e}")

        return f"I've updated your pharmacy to {new_pharmacy_name}.", self.create_status_node()

    async def _check_another_prescription_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Patient wants to check another prescription."""
        logger.info("Flow: Checking another prescription")
        prescriptions = flow_manager.state.get("prescriptions", [])

        if len(prescriptions) > 1:
            return "Of course. Which other medication would you like to check on?", self.create_medication_identification_node()
        else:
            return "I only see one prescription on file for you. Is there something else I can help you with?", self.create_closing_node()

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """End the call."""
        logger.info("Flow: Ending call")

        # Update call status in database
        try:
            patient_id = self._patient_data.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                await db.update_patient(
                    patient_id,
                    {"call_status": "Completed"},
                    self.organization_id,
                )
                logger.info(f"Call status updated to Completed: {patient_id}")
        except Exception as e:
            logger.error(f"Error updating call status: {e}")

        return None, self._create_end_node()

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Transfer to staff member."""
        logger.info("Flow: Transferring to staff")

        # Update call status
        try:
            patient_id = self._patient_data.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                await db.update_patient(
                    patient_id,
                    {"call_status": "Transferred"},
                    self.organization_id,
                )
        except Exception as e:
            logger.error(f"Error updating call status: {e}")

        return None, self._create_transfer_node()
