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

    OpenAI caches prompt prefixes of 1024+ tokens. We send a request
    with the same system prompt structure to prime the cache.
    """
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        global_instructions = f"""You are Jamie, a friendly assistant for {organization_name}.

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
- You MUST verify patient identity before discussing ANY health information
- Never share lab results with unverified callers
- If verification fails, do not provide any lab information"""

        task_context = """# Goal
Determine what the caller needs and route appropriately.

If caller asks about LAB RESULTS, TEST RESULTS, or BLOOD WORK:
→ Proceed to identity verification

If caller needs something ELSE (appointments, billing, prescriptions):
→ Transfer to staff"""

        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": global_instructions},
                {"role": "system", "content": task_context},
                {"role": "user", "content": "Hi, I'm calling about my lab results"},
                {"role": "assistant", "content": "Of course, I can help you with that. For privacy and security, I need to verify your identity first. May I have your first and last name?"},
            ],
            max_tokens=1,
        )
        logger.info("OpenAI connection warmed up with prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class LabResultsFlow:
    """Lab results inquiry flow for inbound patient calls.

    Flow:
    1. Greeting - Answer and identify as clinic, route to verification or staff
    2. Verification - Verify patient identity (name, DOB) against stored record
    3. Results - Communicate results based on status (ready, provider review, pending)
    4. Closing - Ask if anything else, end call
    """

    def __init__(
        self,
        patient_data: Dict[str, Any],
        flow_manager: FlowManager,
        main_llm=None,
        classifier_llm=None,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
        cold_transfer_config: Dict[str, Any] = None,
    ):
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.patient_data = patient_data
        self.organization_name = patient_data.get("organization_name", "Demo Clinic Alpha")
        self.cold_transfer_config = cold_transfer_config or {}

        # Initialize flow state directly in constructor
        self._init_flow_state()

    def _init_flow_state(self):
        """Initialize flow_manager state with patient data."""
        # Patient record (from clinic database)
        self.flow_manager.state["patient_id"] = self.patient_data.get("patient_id")
        self.flow_manager.state["patient_name"] = self.patient_data.get("patient_name", "")
        self.flow_manager.state["date_of_birth"] = self.patient_data.get("date_of_birth", "")
        self.flow_manager.state["medical_record_number"] = self.patient_data.get("medical_record_number", "")
        self.flow_manager.state["phone_number"] = self.patient_data.get("phone_number", "")

        # Lab order information
        self.flow_manager.state["test_type"] = self.patient_data.get("test_type", "")
        self.flow_manager.state["test_date"] = self.patient_data.get("test_date", "")
        self.flow_manager.state["ordering_physician"] = self.patient_data.get("ordering_physician", "")
        self.flow_manager.state["results_status"] = self.patient_data.get("results_status", "")
        self.flow_manager.state["results_summary"] = self.patient_data.get("results_summary", "")
        self.flow_manager.state["provider_review_required"] = self.patient_data.get("provider_review_required", False)
        self.flow_manager.state["callback_timeframe"] = self.patient_data.get("callback_timeframe", "24 to 48 hours")

        # Call outcome tracking
        self.flow_manager.state["identity_verified"] = False
        self.flow_manager.state["results_communicated"] = False

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
        """Global behavioral rules for patient interactions."""
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
- NEVER interpret or diagnose based on lab results
- NEVER share results if provider_review_required is True—only explain the doctor will call
- If you don't have information, say so honestly
- Stay on topic: lab results inquiries only
- If caller is frustrated or asks for a human, transfer them"""

    # ========== Node Creation Functions ==========

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting when patient calls."""
        greeting_text = f"Hello! Thank you for calling {self.organization_name}. How can I help you today?"

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

# Scenario Handling
If caller asks about LAB RESULTS, TEST RESULTS, or BLOOD WORK:
→ Call proceed_to_verification immediately

If caller needs something ELSE (appointments, billing, prescriptions, etc.):
→ Say "Let me connect you with someone who can help with that."
→ Call request_staff

# Example Flow
Caller: "I'm calling to check on my lab results."
→ Call proceed_to_verification

Caller: "I need to schedule an appointment."
→ "Let me connect you with someone who can help with that."
→ Call request_staff

# Guardrails
- Do NOT ask for any personal information yet
- Do NOT discuss lab results until identity is verified
- Route to verification as soon as caller mentions lab/test results

# Error Handling
If you miss what the caller said:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never guess what they need""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_verification",
                    description="""WHEN TO USE: Caller asks about lab results, test results, or blood work.
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
        self.flow_manager.state["test_type"] = "biopsy" if "biopsy" in context.lower() else ""
        self.flow_manager.state["caller_anxious"] = "anxious" in context.lower() or "worried" in context.lower()

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

The caller already explained they're checking on lab results. The previous assistant acknowledged it.
IMMEDIATELY call proceed_to_verification (do NOT speak first - no greeting, no acknowledgment).

The context shows: {context}
Note any urgency or anxiety for when you communicate with them.""",
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
        stored_name = state.get("patient_name", "")
        stored_dob = state.get("date_of_birth", "")

        return NodeConfig(
            name="verification",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""# Goal
Verify the caller's identity before sharing any lab results. This step is important.

# Patient Record on File
- Name: {stored_name}
- Date of Birth: {stored_dob}

# Verification Steps
1. Ask for their first and last name
2. Ask for their date of birth
3. Compare against the record on file
4. Call verify_identity with the information they provide

# Example Flow
You: "For privacy and security, I need to verify your identity first. May I have your first and last name?"
Caller: "Maria Santos"
You: "Thank you, Maria. And what is your date of birth?"
Caller: "March 22nd, 1978"
→ Call verify_identity with name="Maria Santos" and date_of_birth="March 22, 1978"

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
- If caller refuses to verify, explain it's required for privacy and offer to transfer to staff

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
RESULT: Verifies against stored record and proceeds to results if matched.

EXAMPLES:
- Caller says "Maria Santos" and "March 22, 1978" → call with those values
- Caller says "John Doe, born January 5, 1990" → call with name="John Doe", date_of_birth="January 5, 1990" """,
                    properties={
                        "name": {
                            "type": "string",
                            "description": "Caller's full name as stated (first and last)",
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Caller's date of birth in natural format (e.g., 'March 22, 1978')",
                        },
                    },
                    required=["name", "date_of_birth"],
                    handler=self._verify_identity_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_results_node(self) -> NodeConfig:
        """Handle provider review or pending results scenarios.

        Note: Ready results with no review required are handled directly by
        verify_identity handler to guarantee results are spoken.
        """
        state = self.flow_manager.state
        test_type = state.get("test_type", "lab test")
        test_date = state.get("test_date", "")
        ordering_physician = state.get("ordering_physician", "your doctor")
        results_status = state.get("results_status", "")
        provider_review_required = state.get("provider_review_required", False)
        callback_timeframe = state.get("callback_timeframe", "24 to 48 hours")
        phone_number = state.get("phone_number", "")
        phone_last4 = phone_number[-4:] if len(phone_number) >= 4 else ""

        return NodeConfig(
            name="results",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""# Context
The patient has been verified. Handle their lab results inquiry.

# Lab Order Information
- Test Type: {test_type}
- Test Date: {test_date}
- Ordering Physician: {ordering_physician}
- Results Status: {results_status}
- Provider Review Required: {provider_review_required}
- Callback Timeframe: {callback_timeframe}
- Phone on File (last 4): {phone_last4}

# Scenario Handling

## Provider Review Required
If provider_review_required is True:
→ Do NOT share any results. This step is important.
→ Explain: "{ordering_physician} needs to review these results before they can be shared."
→ Tell them: "The doctor will call you within {callback_timeframe} to discuss."
→ If worried: "Provider review is standard procedure for certain results."
→ Confirm callback: "Is the number ending in {phone_last4} still the best to reach you?"
→ Call confirm_callback after they respond

## Results Pending
If results_status is "Pending":
→ Explain: "Your {test_type} from {test_date} is still being processed."
→ Offer callback: "Would you like us to call you when they're ready?"
→ If yes, confirm phone and call confirm_callback

# Example Flow (Provider Review)
You: "I can see your results have been received. However, {ordering_physician} needs to review them before I can share the details. The doctor will call you within {callback_timeframe}. Is the number ending in {phone_last4} the best to reach you?"
Patient: "Yes, that's my cell."
→ Call confirm_callback with confirmed=true

# Example Flow (Pending)
You: "Your {test_type} is still being processed. Would you like us to call when it's ready?"
Patient: "Yes please."
You: "Is the number ending in {phone_last4} still good?"
Patient: "Yes."
→ Call confirm_callback with confirmed=true

# Guardrails
- NEVER share results when provider_review_required is True. This step is important.
- If caller presses for details: "I understand, but the doctor needs to review first."
- If frustrated, offer transfer: call request_staff

# Error Handling
If you miss what caller said: "I'm sorry, could you repeat that?"
If caller gives new number you didn't catch: "Could you say that number again?" """,
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="confirm_callback",
                    description="""WHEN TO USE: After confirming callback phone number with patient.
RESULT: Records callback preference and proceeds to closing.

EXAMPLES:
- "Yes, that's my cell" → confirmed=true
- "Call me at 555-1234" → confirmed=true, new_number="5551234"
- "No, don't call" → confirmed=false""",
                    properties={
                        "confirmed": {
                            "type": "boolean",
                            "description": "Whether patient confirmed/wants callback",
                        },
                        "new_number": {
                            "type": "string",
                            "description": "New phone number if different (digits only), or empty",
                        },
                    },
                    required=["confirmed"],
                    handler=self._confirm_callback_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_closing_node(self) -> NodeConfig:
        """Ask if anything else and end call."""
        return NodeConfig(
            name="closing",
            task_messages=[
                {
                    "role": "system",
                    "content": """# Goal
Wrap up the call professionally and check if patient needs anything else.

# IMPORTANT: Do NOT proactively repeat information
Do NOT summarize or repeat results on your own. Just ask if there's anything else.
HOWEVER: If the patient asks you to repeat something, you SHOULD repeat it. Use the conversation history to find what was said.

# Example Flow
You: "Is there anything else I can help you with today?"

If patient says no/goodbye:
→ "Thank you for calling. Take care!"
→ Call end_call

If patient has another question about lab results:
→ Answer if you can, then ask again: "Anything else?"

If patient needs something else (appointments, billing, etc.):
→ "Let me connect you with someone who can help with that."
→ Call request_staff

# Guardrails
- Keep the closing brief and warm
- Do NOT repeat or summarize results—they've already been shared
- Don't introduce new topics
- If they have more lab questions you can't answer, offer to transfer

# Error Handling
If you miss what the caller said:
- Ask naturally: "I'm sorry, could you repeat that?"
- Never assume they said goodbye""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="end_call",
                    description="""WHEN TO USE: Patient confirms they have no more questions and says goodbye.
RESULT: Ends the call gracefully.""",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
                self._get_request_staff_function(),
            ],
            respond_immediately=True,
        )

    def create_completion_node(self) -> NodeConfig:
        """After delivering results, ask if there's anything else and handle follow-up requests."""
        # Get practice info for simple questions
        practice_info = self.patient_data.get("practice_info", {})
        office_hours = practice_info.get("office_hours", "Monday through Friday, 8 AM to 5 PM")
        location = practice_info.get("location", "")
        parking = practice_info.get("parking", "")

        # Build practice info text
        practice_facts = []
        if office_hours:
            practice_facts.append(f"- Office hours: {office_hours}")
        if location:
            practice_facts.append(f"- Location: {location}")
        if parking:
            practice_facts.append(f"- Parking: {parking}")

        practice_info_text = "\n".join(practice_facts) if practice_facts else "- Contact the front desk for practice information"

        return NodeConfig(
            name="completion",
            role_messages=[
                {
                    "role": "system",
                    "content": self._get_global_instructions(),
                }
            ],
            task_messages=[
                {
                    "role": "system",
                    "content": f"""# Goal
The lab results inquiry is complete. Thank the caller and check if they need anything else.

# Questions You CAN Answer Directly
{practice_info_text}

# Scenario Handling

If patient says NO / GOODBYE:
→ Say something warm like "Take care!"
→ Call end_call

If patient asks a SIMPLE QUESTION (hours, location, parking):
→ Answer directly
→ Ask "Is there anything else I can help with?"

If patient needs SCHEDULING (book, cancel, reschedule appointment):
→ Say "I can help with that."
→ Call route_to_workflow with workflow="scheduling"

If patient needs PRESCRIPTION help (refill, medication status):
→ Say "Let me connect you with someone who can help with that."
→ Call route_to_workflow with workflow="prescription_status"

If patient needs BILLING or asks for a HUMAN:
→ Say "Let me transfer you to our billing team." or "Let me connect you with someone."
→ Call request_staff with appropriate department

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
- If caller is frustrated or asks for a human, route to front_desk immediately""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="route_to_workflow",
                    description="""Route caller to an AI-powered workflow.

WHEN TO USE: Caller asks about scheduling or prescriptions.
RESULT: Hands off to specialized AI workflow (no phone transfer).

IMPORTANT: The caller is already verified - context carries through.

EXAMPLES:
- workflow="scheduling", reason="follow-up after lab results"
- workflow="prescription_status", reason="refill inquiry after lab call" """,
                    properties={
                        "workflow": {
                            "type": "string",
                            "enum": ["scheduling", "prescription_status"],
                            "description": "Workflow: scheduling (appointments) or prescription_status (refills/medications)",
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
                    name="request_staff",
                    description="""Transfer caller to human staff via phone.

WHEN TO USE: Caller needs billing help, asks for a human, or has unclear/complex needs.
RESULT: Initiates SIP transfer to staff phone number.

EXAMPLES:
- "I have a billing question" → department="billing"
- "Can I speak to someone?" → department="front_desk"
- Unclear request → department="front_desk" """,
                    properties={
                        "department": {
                            "type": "string",
                            "enum": ["billing", "front_desk"],
                            "description": "Department: billing (payments/insurance), front_desk (general/complex/unclear)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for transfer",
                        },
                    },
                    required=["department"],
                    handler=self._request_staff_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call gracefully.

WHEN TO USE: Caller says goodbye or confirms no more questions.
RESULT: Saves transcript and ends the call.

EXAMPLES:
- "No, that's all, thanks!" → call end_call
- "Bye!" → call end_call""",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
            ],
            respond_immediately=True,
            pre_actions=[
                {"type": "tts_say", "text": "Thanks for your patience with that. Is there anything else I can help you with today?"},
            ],
        )

    def _create_end_node(self) -> NodeConfig:
        """Terminal node that ends the conversation."""
        return NodeConfig(
            name="end",
            task_messages=[
                {
                    "role": "system",
                    "content": "Thank the patient and say goodbye warmly.",
                }
            ],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )

    def create_verification_failed_node(self) -> NodeConfig:
        """Node for when identity verification fails."""
        return NodeConfig(
            name="verification_failed",
            task_messages=[
                {
                    "role": "system",
                    "content": """The information provided doesn't match our records.

Apologize and offer to transfer to staff who can help:
"I'm sorry, but I wasn't able to verify your identity with the information provided. For your security, let me connect you with a staff member who can assist you further."

Then call request_staff with patient_confirmed=true.""",
                }
            ],
            functions=[
                self._get_request_staff_function(),
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

If caller has a question you can answer:
→ Answer it, then ask if there's anything else""",
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

    # ========== Function Handlers ==========

    async def _proceed_to_verification_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str | None, NodeConfig]:
        """Transition from greeting to verification."""
        logger.info("Flow: Proceeding to identity verification")
        # Return None for message - let the LLM generate the verification request naturally
        return None, self.create_verification_node()

    async def _verify_identity_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str | None, NodeConfig]:
        """Verify caller identity against stored patient record."""
        provided_name = args.get("name", "")
        provided_dob = args.get("date_of_birth", "")

        stored_name = flow_manager.state.get("patient_name", "")
        stored_dob = flow_manager.state.get("date_of_birth", "")

        # Use helper methods for normalization
        provided_name_normalized = self._normalize_name(provided_name)
        stored_name_normalized = self._normalize_name(stored_name)
        provided_dob_normalized = self._normalize_dob(provided_dob)
        stored_dob_normalized = self._normalize_dob(stored_dob)

        # Check matches
        name_match = provided_name_normalized == stored_name_normalized if stored_name_normalized else False
        dob_match = provided_dob_normalized == stored_dob_normalized if stored_dob_normalized else False

        logger.info(f"Flow: Identity verification - name_match={name_match}, dob_match={dob_match}")

        if name_match and dob_match:
            # Verification successful
            flow_manager.state["identity_verified"] = True

            # Write to database immediately
            patient_id = flow_manager.state.get("patient_id")
            if patient_id:
                try:
                    db = get_async_patient_db()
                    await db.update_field(patient_id, "identity_verified", True, self.organization_id)
                except Exception as e:
                    logger.error(f"Error updating identity_verified: {e}")

            # Extract first name for personalized response
            first_name = provided_name.strip().split()[0].title() if provided_name.strip() else "there"

            logger.info(f"Flow: Identity verified for {first_name}")

            # Check results status and route appropriately
            results_status = flow_manager.state.get("results_status", "")
            provider_review_required = flow_manager.state.get("provider_review_required", False)
            results_summary = flow_manager.state.get("results_summary", "")
            test_type = flow_manager.state.get("test_type", "lab test")
            test_date = flow_manager.state.get("test_date", "")

            # Scenario 1: Results ready AND no provider review required
            # → Share results immediately (handler guarantees this is spoken)
            if results_status.lower() in ["ready", "available"] and not provider_review_required and results_summary:
                flow_manager.state["results_communicated"] = True
                if patient_id:
                    try:
                        db = get_async_patient_db()
                        await db.update_field(patient_id, "results_communicated", True, self.organization_id)
                    except Exception as e:
                        logger.error(f"Error updating results_communicated: {e}")
                logger.info("Flow: Results communicated to patient (ready, no review required)")

                message = f"Thank you, {first_name}. I found your record. I can see you had a {test_type}"
                if test_date:
                    message += f" on {test_date}"
                message += f". Your results are in and show: {results_summary}"

                return message, self.create_completion_node()

            # Scenario 2: Provider review required OR Scenario 3: Pending
            # → Go to results node for callback handling
            return f"Thank you, {first_name}. I found your record. Let me check the status of your lab results.", self.create_results_node()
        else:
            # Verification failed
            logger.warning(f"Flow: Identity verification failed - provided: {provided_name}, {provided_dob}")
            return None, self.create_verification_failed_node()

    async def _confirm_callback_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Confirm callback phone number preference."""
        confirmed = args.get("confirmed", False)
        new_number = args.get("new_number", "").strip()

        if confirmed:
            callback_timeframe = flow_manager.state.get("callback_timeframe", "24 to 48 hours")

            # Update phone number if new one provided
            update_fields = {"callback_confirmed": True}
            if new_number:
                # Normalize to digits only
                new_number_digits = ''.join(c for c in new_number if c.isdigit())
                flow_manager.state["phone_number"] = new_number_digits
                update_fields["caller_phone_number"] = new_number_digits
                logger.info(f"Flow: Callback number updated to {new_number_digits[-4:]}")

            logger.info("Flow: Callback confirmed")

            # Write to database immediately
            patient_id = flow_manager.state.get("patient_id")
            if patient_id:
                try:
                    db = get_async_patient_db()
                    await db.update_patient(patient_id, update_fields, self.organization_id)
                except Exception as e:
                    logger.error(f"Error updating callback info: {e}")

            response = f"I've confirmed your callback number. You should expect a call within {callback_timeframe}."
        else:
            logger.info("Flow: Patient declined callback")
            response = "Understood. You can always call us back to check on your results."

        return response, self.create_completion_node()

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """End the call gracefully."""
        logger.info("Flow: Ending call")
        patient_id = flow_manager.state.get("patient_id")
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

        # Get staff number from config
        staff_number = self.cold_transfer_config.get("staff_number") if hasattr(self, 'cold_transfer_config') else None

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
                patient_id = flow_manager.state.get("patient_id")
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

        staff_number = self.cold_transfer_config.get("staff_number") if hasattr(self, 'cold_transfer_config') else None

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

    async def _route_to_workflow_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Route to an AI workflow (same call, no phone transfer)."""
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")

        flow_manager.state["routed_to"] = f"{workflow} (AI)"

        logger.info(f"Flow: Routing to {workflow} workflow - reason: {reason}")

        if workflow == "scheduling":
            return await self._handoff_to_scheduling(flow_manager, reason)
        elif workflow == "prescription_status":
            return await self._handoff_to_prescription_status(flow_manager, reason)
        else:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

    async def _handoff_to_scheduling(
        self, flow_manager: FlowManager, reason: str
    ) -> tuple[str, NodeConfig]:
        """Hand off to PatientSchedulingFlow with gathered context."""
        from clients.demo_clinic_alpha.patient_scheduling.flow_definition import PatientSchedulingFlow

        scheduling_flow = PatientSchedulingFlow(
            patient_data=self.patient_data,
            flow_manager=flow_manager,
            main_llm=self.main_llm,
            context_aggregator=self.context_aggregator,
            transport=self.transport,
            pipeline=self.pipeline,
            organization_id=self.organization_id,
            cold_transfer_config=self.cold_transfer_config,
        )

        logger.info(f"Flow: Handing off to PatientSchedulingFlow with context: {reason}")

        # Use handoff entry point with context (no greeting, context-aware)
        return None, scheduling_flow.create_handoff_entry_node(context=reason)

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
- Caller needs help with something other than lab results
- Caller explicitly asks for a human
- Caller is frustrated
- Verification failed

EXAMPLES:
- Caller asks about appointments → call with reason="scheduling"
- Caller says "I want to talk to a person" → call with patient_confirmed=true
- Urgent medical concern → call with urgent=true
- Caller needs billing help → call with reason="billing" """,
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
                    "description": "Brief reason for transfer (e.g., 'scheduling', 'billing', 'frustrated', 'verification_failed')",
                },
            },
            required=[],
            handler=self._request_staff_handler,
        )
