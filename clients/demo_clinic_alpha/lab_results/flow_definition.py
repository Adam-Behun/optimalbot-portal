from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


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
        flow_manager: FlowManager = None,
        main_llm=None,
        classifier_llm=None,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
    ):
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

        # Lab order information
        self.flow_manager.state["test_type"] = self._patient_data.get("test_type", "")
        self.flow_manager.state["test_date"] = self._patient_data.get("test_date", "")
        self.flow_manager.state["ordering_physician"] = self._patient_data.get("ordering_physician", "")
        self.flow_manager.state["results_status"] = self._patient_data.get("results_status", "")
        self.flow_manager.state["results_summary"] = self._patient_data.get("results_summary", "")
        self.flow_manager.state["provider_review_required"] = self._patient_data.get("provider_review_required", False)
        self.flow_manager.state["callback_timeframe"] = self._patient_data.get("callback_timeframe", "24 to 48 hours")

        # Call outcome tracking
        self.flow_manager.state["identity_verified"] = False
        self.flow_manager.state["results_communicated"] = False

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
- Route to verification as soon as caller mentions lab/test results""",
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

# Guardrails
- Collect BOTH name AND date of birth before calling verify_identity. This step is important.
- Do NOT reveal any patient information during verification
- Do NOT say whether the name or DOB matches until both are collected
- Be patient if caller needs to repeat information
- If caller refuses to verify, explain it's required for privacy and offer to transfer to staff""",
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
        """Communicate lab results or status to patient based on results_status."""
        state = self.flow_manager.state
        test_type = state.get("test_type", "lab test")
        test_date = state.get("test_date", "")
        ordering_physician = state.get("ordering_physician", "your doctor")
        results_status = state.get("results_status", "")
        results_summary = state.get("results_summary", "")
        provider_review_required = state.get("provider_review_required", False)
        callback_timeframe = state.get("callback_timeframe", "24 to 48 hours")
        phone_number = state.get("phone_number", "")
        phone_last4 = phone_number[-4:] if len(phone_number) >= 4 else ""

        return NodeConfig(
            name="results",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""# Goal
Communicate lab results status to the verified patient appropriately based on the scenario.

# Lab Order Information
- Test Type: {test_type}
- Test Date: {test_date}
- Ordering Physician: {ordering_physician}
- Results Status: {results_status}
- Provider Review Required: {provider_review_required}
- Results Summary: {results_summary}
- Callback Timeframe: {callback_timeframe}
- Phone on File (last 4): {phone_last4}

# Scenario Handling

## Scenario 1: Results Available AND Provider Review NOT Required
If results_status is "Ready" or "Available" (not "Pending") and provider_review_required is False:
→ Share the test information and results_summary
→ Example: "I can see your {test_type} results from {test_date} are in. The results show {results_summary}."
→ Call mark_results_communicated

## Scenario 2: Provider Review Required
If provider_review_required is True:
→ Do NOT share the results_summary. This step is important.
→ Explain that {ordering_physician} needs to review the results first
→ Tell them the doctor will call within {callback_timeframe}
→ If they ask "Is something wrong?" or seem worried: "Provider review is standard procedure for certain types of results. {ordering_physician} wants to ensure they can answer any questions you might have."
→ Confirm callback phone number: "Is the phone number ending in {phone_last4} still the best number to reach you?"
→ Call confirm_callback with their response

## Scenario 3: Results Pending
If results_status is "Pending":
→ Explain results are still being processed
→ Give expected timeline if known
→ Offer to note that they should be called when results arrive
→ Example: "Your {test_type} from {test_date} is still being processed. Results typically take 5 to 7 business days. Would you like us to call you when they're ready?"
→ If yes, confirm phone number and call confirm_callback

# Example Conversations

Example (Ready, no review):
"Let me check the status of your lab results. I can see you had a {test_type} performed on {test_date}, ordered by {ordering_physician}. The results are in and show {results_summary}."
→ Call mark_results_communicated

Example (Provider review required):
"I can see your lab results have been received from the lab. However, {ordering_physician} needs to review these results before they can be shared with you. The doctor will call you personally within {callback_timeframe} to discuss the results. Is the phone number ending in {phone_last4} still the best number to reach you?"

# Guardrails
- NEVER share results_summary if provider_review_required is True. This step is important.
- NEVER interpret or diagnose—just relay the summary as written
- If caller presses for more details you don't have, be honest: "That's all the information I have available"
- If caller is upset about waiting, empathize but stay on script""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="mark_results_communicated",
                    description="""WHEN TO USE: After successfully sharing lab results with the patient (only when results are ready AND no provider review required).
RESULT: Records that results were communicated and proceeds to closing.""",
                    properties={},
                    required=[],
                    handler=self._mark_results_communicated_handler,
                ),
                FlowsFunctionSchema(
                    name="confirm_callback",
                    description="""WHEN TO USE: After confirming the callback phone number with patient (for provider review or pending scenarios).
RESULT: Records callback preference and proceeds to closing.

EXAMPLES:
- Patient confirms "Yes, that's my cell" → confirmed=true
- Patient gives different number "Actually, call me at 555-1234" → confirmed=true, new_number="5551234"
- Patient declines callback → confirmed=false""",
                    properties={
                        "confirmed": {
                            "type": "boolean",
                            "description": "Whether patient confirmed/wants callback",
                        },
                        "new_number": {
                            "type": "string",
                            "description": "New phone number if patient provided a different one (digits only), or empty",
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
- Don't introduce new topics
- If they have more lab questions you can't answer, offer to transfer""",
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

    # ========== Function Handlers ==========

    async def _proceed_to_verification_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Transition from greeting to verification."""
        logger.info("Flow: Proceeding to identity verification")
        # Return None for message - let the LLM generate the verification request naturally
        return None, self.create_verification_node()

    async def _verify_identity_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Verify caller identity against stored patient record."""
        provided_name = args.get("name", "").strip().lower()
        provided_dob = args.get("date_of_birth", "").strip()

        stored_name = flow_manager.state.get("patient_name", "").lower()
        stored_dob = flow_manager.state.get("date_of_birth", "")

        # Normalize DOB for comparison (handle various formats)
        from backend.utils import parse_natural_date
        provided_dob_normalized = parse_natural_date(provided_dob) or provided_dob
        stored_dob_normalized = parse_natural_date(stored_dob) or stored_dob

        # Check if name matches (allow for "Last, First" vs "First Last" formats)
        name_match = False
        if stored_name:
            # Handle "Last, First" format
            if "," in stored_name:
                parts = [p.strip() for p in stored_name.split(",")]
                stored_name_reversed = f"{parts[1]} {parts[0]}".lower() if len(parts) == 2 else stored_name
                name_match = provided_name == stored_name or provided_name == stored_name_reversed
            else:
                name_match = provided_name == stored_name

        dob_match = provided_dob_normalized == stored_dob_normalized if stored_dob_normalized else False

        logger.info(f"Flow: Identity verification - name_match={name_match}, dob_match={dob_match}")

        if name_match and dob_match:
            # Verification successful
            flow_manager.state["identity_verified"] = True

            # Update database
            try:
                patient_id = flow_manager.state.get("patient_id")
                if patient_id:
                    db = get_async_patient_db()
                    await db.update_patient(
                        patient_id,
                        {"identity_verified": True},
                        self.organization_id,
                    )
                    logger.info(f"Database updated: identity_verified=True for patient {patient_id}")
            except Exception as e:
                logger.error(f"Error updating identity verification in database: {e}")

            # Extract first name for personalized response
            first_name = provided_name.split()[0].title() if provided_name else "there"

            logger.info(f"Flow: Identity verified for {first_name}")
            return f"Thank you, {first_name}. I found your record. Let me check the status of your lab results.", self.create_results_node()
        else:
            # Verification failed
            logger.warning(f"Flow: Identity verification failed - provided: {provided_name}, {provided_dob}")
            return None, self.create_verification_failed_node()

    async def _mark_results_communicated_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Mark that results were successfully communicated to patient."""
        flow_manager.state["results_communicated"] = True
        logger.info("Flow: Results communicated to patient")

        # Update database
        try:
            patient_id = flow_manager.state.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                await db.update_patient(
                    patient_id,
                    {"results_communicated": True},
                    self.organization_id,
                )
                logger.info(f"Database updated: results_communicated=True for patient {patient_id}")
        except Exception as e:
            logger.error(f"Error updating results_communicated in database: {e}")

        return None, self.create_closing_node()

    async def _confirm_callback_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Confirm callback phone number preference."""
        confirmed = args.get("confirmed", False)
        new_number = args.get("new_number", "").strip()

        if confirmed:
            callback_timeframe = flow_manager.state.get("callback_timeframe", "24 to 48 hours")

            # Update phone number if new one provided
            if new_number:
                # Normalize to digits only
                new_number_digits = ''.join(c for c in new_number if c.isdigit())
                flow_manager.state["phone_number"] = new_number_digits
                logger.info(f"Flow: Callback number updated to {new_number_digits[-4:]}")

            logger.info("Flow: Callback confirmed")

            # Update database
            try:
                patient_id = flow_manager.state.get("patient_id")
                if patient_id:
                    db = get_async_patient_db()
                    update_fields = {"callback_confirmed": True}
                    if new_number:
                        update_fields["caller_phone_number"] = flow_manager.state.get("phone_number")
                    await db.update_patient(patient_id, update_fields, self.organization_id)
                    logger.info(f"Database updated: callback confirmed for patient {patient_id}")
            except Exception as e:
                logger.error(f"Error updating callback confirmation in database: {e}")

            response = f"I've confirmed your callback number. You should expect a call within {callback_timeframe}."
        else:
            logger.info("Flow: Patient declined callback")
            response = "Understood. You can always call us back to check on your results."

        return response, self.create_closing_node()

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
        """Transfer to staff member."""
        patient_confirmed = args.get("patient_confirmed", False)
        reason = args.get("reason", "general inquiry")

        logger.info(f"Flow: Staff transfer requested - reason: {reason}, confirmed: {patient_confirmed}")

        # For now, just transition to transfer node
        # In production, would initiate actual SIP transfer
        if self.transport and hasattr(self.transport, 'sip_call_transfer'):
            try:
                if self.pipeline:
                    self.pipeline.transfer_in_progress = True
                # Note: staff_number would come from cold_transfer_config in production
                logger.info("Flow: Would initiate SIP transfer here")
            except Exception as e:
                logger.error(f"Flow: Transfer failed: {e}")

        return None, self.create_transfer_initiated_node()

    def _get_request_staff_function(self) -> FlowsFunctionSchema:
        """Return the request_staff function schema for use in multiple nodes."""
        return FlowsFunctionSchema(
            name="request_staff",
            description="""WHEN TO USE: Caller needs help with something other than lab results, or explicitly asks for a human, or is frustrated.
RESULT: Transfers call to staff member.

EXAMPLES:
- Caller asks about appointments → call with reason="scheduling"
- Caller says "I want to talk to a person" → call with patient_confirmed=true
- Caller needs billing help → call with reason="billing" """,
            properties={
                "reason": {
                    "type": "string",
                    "description": "Brief reason for transfer (e.g., 'scheduling', 'billing', 'frustrated', 'general')",
                },
                "patient_confirmed": {
                    "type": "boolean",
                    "description": "Set true if patient explicitly asked for human/staff transfer",
                },
            },
            required=[],
            handler=self._request_staff_handler,
        )
