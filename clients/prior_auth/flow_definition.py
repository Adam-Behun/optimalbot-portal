import logging
import re
from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema, ContextStrategy, ContextStrategyConfig
from pipecat.frames.frames import ManuallySwitchServiceFrame, LLMMessagesAppendFrame
from pipecat.processors.frame_processor import FrameDirection
from backend.models import get_async_patient_db

logger = logging.getLogger(__name__)


class PriorAuthFlow:

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager,
                 main_llm, classifier_llm, context_aggregator=None, transport=None, pipeline=None):
        self.patient_data = patient_data
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline

        # Format fields for TTS pronunciation (happens ONCE at initialization)
        self._format_speech_fields()

    def _format_speech_fields(self):
        """Format patient data fields with Cartesia <spell> tags for pronunciation.

        This runs ONCE at flow initialization, adding '_speech' versions of fields
        that need to be spelled out. Zero runtime overhead during conversation.
        """
        # Phone numbers: spell with breaks between segments
        if 'insurance_phone' in self.patient_data:
            self.patient_data['insurance_phone_speech'] = self._format_phone(
                self.patient_data['insurance_phone']
            )

        if 'supervisor_phone' in self.patient_data and self.patient_data['supervisor_phone']:
            self.patient_data['supervisor_phone_speech'] = self._format_phone(
                self.patient_data['supervisor_phone']
            )

        # Member ID: spell out alphanumeric
        if 'insurance_member_id' in self.patient_data:
            member_id = self.patient_data['insurance_member_id']
            self.patient_data['insurance_member_id_speech'] = f"<spell>{member_id}</spell>"

        # NPI: spell with breaks every 3 digits
        if 'provider_npi' in self.patient_data:
            npi = self.patient_data['provider_npi']
            if len(npi) == 10 and npi.isdigit():
                self.patient_data['provider_npi_speech'] = (
                    f"<spell>{npi[:3]}</spell><break time=\"150ms\"/>"
                    f"<spell>{npi[3:6]}</spell><break time=\"150ms\"/>"
                    f"<spell>{npi[6:]}</spell>"
                )
            else:
                self.patient_data['provider_npi_speech'] = f"<spell>{npi}</spell>"

        # CPT code: spell entire code
        if 'cpt_code' in self.patient_data:
            cpt = self.patient_data['cpt_code']
            self.patient_data['cpt_code_speech'] = f"<spell>{cpt}</spell>"

    def _format_reference_number(self, ref_number: str) -> str:
        """Format reference/authorization number with <spell> tags and breaks.

        Reference numbers can be alphanumeric with various formats.
        This method groups characters in chunks of 3 for clarity with pauses between segments.
        """
        if not ref_number:
            return ""

        # Remove any whitespace or special characters for processing
        cleaned = re.sub(r'[^A-Za-z0-9]', '', ref_number)

        if not cleaned:
            return f"<spell>{ref_number}</spell>"

        # 5 or less: spell as one unit
        if len(cleaned) <= 5:
            return f"<spell>{cleaned}</spell>"

        # 6 or more: break into groups of 3
        chunks = [cleaned[i:i+3] for i in range(0, len(cleaned), 3)]
        formatted_chunks = [f"<spell>{chunk}</spell>" for chunk in chunks]
        return "<break time=\"200ms\"/>".join(formatted_chunks)

    def _format_phone(self, phone: str) -> str:
        """Format phone number with <spell> tags and breaks."""
        # Remove +1 prefix and any formatting
        cleaned = re.sub(r'[^\d]', '', phone)
        if cleaned.startswith('1'):
            cleaned = cleaned[1:]

        if len(cleaned) == 10:
            # Format: (123) <break> 456 <break> 7890
            return (
                f"<spell>({cleaned[:3]})</spell><break time=\"200ms\"/>"
                f"<spell>{cleaned[3:6]}</spell><break time=\"200ms\"/>"
                f"<spell>{cleaned[6:]}</spell>"
            )
        return f"<spell>{phone}</spell>"

    def _get_global_instructions(self) -> str:
        """Global behavioral rules applied to all states."""
        facility = self.patient_data.get('facility_name')
        insurance_company = self.patient_data.get('insurance_company_name')

        return f"""PATIENT INFORMATION (USE EXACT VALUES):
- Patient Name: {self.patient_data.get('patient_name')}
- Date of Birth: {self.patient_data.get('date_of_birth')}
- Facility: {facility}
- Insurance Company: {insurance_company}
- Member ID: {self.patient_data.get('insurance_member_id')}
- Insurance Phone: {self.patient_data.get('insurance_phone')}
- CPT Code: {self.patient_data.get('cpt_code')}
- Provider NPI: {self.patient_data.get('provider_npi')}
- Provider Name: {self.patient_data.get('provider_name')}
- Appointment Time: {self.patient_data.get('appointment_time')}

PRONUNCIATION GUIDE - When asked to SPELL OUT or REPEAT information:
- Member ID (spelled): {self.patient_data.get('insurance_member_id_speech', self.patient_data.get('insurance_member_id'))}
- Insurance Phone (spelled): {self.patient_data.get('insurance_phone_speech', self.patient_data.get('insurance_phone'))}
- CPT Code (spelled): {self.patient_data.get('cpt_code_speech', self.patient_data.get('cpt_code'))}
- Provider NPI (spelled): {self.patient_data.get('provider_npi_speech', self.patient_data.get('provider_npi'))}

CRITICAL BEHAVIORAL RULES:
1. AI TRANSPARENCY: You are a Virtual Assistant. Disclose this in your initial greeting. Say "I'm Alexandra, a Virtual Assistant helping {facility}" when first introducing yourself. Never pretend to be human.

2. LANGUAGE: Speak ONLY in English. If the caller speaks another language, politely redirect in English: "I apologize, but I can only communicate in English for this verification."

3. TONE: Maintain a professional, courteous medical office assistant tone at ALL TIMES. Never mirror the caller's informal language, slang, accent, or emotional tone. Stay calm and professional even if the caller is frustrated, rude, or casual.

4. DATA ACCURACY: ONLY use patient information explicitly provided in the PATIENT INFORMATION section above. NEVER guess, estimate, or invent any details. If you don't have information, say "I don't have that information available" rather than making it up.

5. STAY ON TOPIC: This is an insurance verification call. Do not engage in personal conversations, medical advice, billing disputes, or any topics unrelated to insurance verification. If caller goes off-topic, politely redirect: "I appreciate that, but I'm only able to assist with the insurance verification today."

6. SPEAKING STYLE: Use complete, grammatically correct sentences. Do not use slang, informal contractions beyond standard ones, or casual phrases. Maintain consistency regardless of how the caller speaks.

7. ANSWERING QUESTIONS: You can answer questions about ANY of the patient information fields listed above at ANY time during the call, regardless of the current conversation state. Always use the exact values provided.

8. HESITATION & TRUST DETECTION: If the representative shows ANY signs of hesitation, discomfort, or distrust about speaking with AI, proactively offer supervisor transfer:
   - Hesitation signals: "Are you AI?", "Is this a bot?", "Can I trust this?", "I'm not comfortable with this", "This doesn't sound right", "I'd rather speak to a person", long pauses after disclosure, skeptical tone
   - Response pattern: Acknowledge their concern professionally, then offer: "I completely understand. Would you like to speak with my supervisor?"
   - Do NOT be defensive or try to convince them - immediately offer the human option"""

    def create_greeting_node_after_ivr_completed(self) -> NodeConfig:
        """
        Greeting node used after IVR navigation completes.

        - Waits for user to speak first (respond_immediately=False)
        - Resets context to clear IVR navigation messages (context_strategy=RESET)
        """
        facility = self.patient_data.get('facility_name', 'a medical facility')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="greeting_after_ivr",
            role_messages=[{
                "role": "system",
                "content": f"""You are Alexandra, a Virtual Assistant from {facility}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""YOU called the insurance company representative. After navigating the IVR system, a human has now answered. Greet them, disclose you're a Virtual Assistant, and provide initial patient context.

STEP 1 - ANALYZE THE CONVERSATION HISTORY:
- Look through ALL previous user messages in this conversation
- Check if the representative introduced themselves with a name (e.g., "Hello, this is Jennifer", "This is Adam from Aetna", "Jennifer speaking")
- Extract ONLY the first name if present (e.g., "Jennifer" or "Adam")

STEP 2 - CONSTRUCT YOUR GREETING:
- If you found a name → Start with "Hi [FirstName], this is Alexandra from {facility}."
- If NO name was found → Start with "Hi, this is Alexandra from {facility}."
- Then add: "Can you help me verify eligibility and benefits for a patient?"

EXAMPLES:
- They said "Hello, this is Adam from Aetna" → You respond: "Hi Adam, this is Alexandra from {facility}. Can you help me verify eligibility and benefits for a patient?"
- They said "Hello?" → You respond: "Hi, this is Alexandra from {facility}. Can you help me verify eligibility and benefits for a patient?"

CRITICAL RULES:
- FIRST: Speak the greeting message out loud (this will be converted to speech)
- THEN: After the greeting text is complete, call the proceed_to_verification() function
- You must output text before calling any function"""
            }],
            functions=[FlowsFunctionSchema(
                name="proceed_to_verification",
                description="Transition from greeting to verification node after initial greeting is complete.",
                properties={},
                required=[],
                handler=self._proceed_to_verification_handler
            )],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET
            )
        )

    def create_greeting_node_without_ivr(self) -> NodeConfig:
        """
        Greeting node used when human answers directly (no IVR navigation).

        - Speaks immediately (respond_immediately=True)
        - Appends to existing context (default context strategy)
        """
        facility = self.patient_data.get('facility_name', 'a medical facility')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="greeting_without_ivr",
            role_messages=[{
                "role": "system",
                "content": f"""You are Alexandra, a Virtual Assistant from {facility}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""YOU called the insurance company representative. A human answered directly. Greet them, disclose you're a Virtual Assistant, and provide initial patient context.

STEP 1 - ANALYZE THE CONVERSATION HISTORY:
- Look through ALL previous user messages in this conversation
- Check if the representative introduced themselves with a name (e.g., "Hello, this is Jennifer", "This is Adam from Aetna", "Jennifer speaking")
- Extract ONLY the first name if present (e.g., "Jennifer" or "Adam")

STEP 2 - CONSTRUCT YOUR GREETING:
- If you found a name → Start with "Hi [FirstName], this is Alexandra from {facility}."
- If NO name was found → Start with "Hi, this is Alexandra from {facility}."
- Then add: "Can you help me verify eligibility and benefits for a patient?"

EXAMPLES:
- They said "Hello, this is Adam from Aetna" → You respond: "Hi Adam, this is Alexandra from {facility}. Can you help me verify eligibility and benefits for a patient?"
- They said "Hello?" → You respond: "Hi, this is Alexandra from {facility}. Can you help me verify eligibility and benefits for a patient?"

CRITICAL RULES:
- FIRST: Speak the greeting message out loud (this will be converted to speech)
- THEN: After the greeting text is complete, call the proceed_to_verification() function
- You must output text before calling any function"""
            }],
            functions=[FlowsFunctionSchema(
                name="proceed_to_verification",
                description="Transition from greeting to verification node after initial greeting is complete.",
                properties={},
                required=[],
                handler=self._proceed_to_verification_handler
            )],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_verification_node(self) -> NodeConfig:
        patient_name = self.patient_data.get('patient_name')
        dob = self.patient_data.get('date_of_birth')
        member_id = self.patient_data.get('insurance_member_id')
        cpt_code = self.patient_data.get('cpt_code')
        provider_npi = self.patient_data.get('provider_npi')
        provider_name = self.patient_data.get('provider_name')
        facility = self.patient_data.get('facility_name')
        insurance_company = self.patient_data.get('insurance_company_name')
        appointment_time = self.patient_data.get('appointment_time')
        patient_id = self.patient_data.get('patient_id')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="verification",
            role_messages=[{
                "role": "system",
                "content": f"""You are Alexandra, a Virtual Assistant from {facility} verifying insurance benefits.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You are in a conversation with an insurance representative. You have already introduced yourself in the greeting state.

FIRST: ANALYZE THE CONVERSATION CONTEXT AND REPRESENTATIVE'S RESPONSE
- Review ALL previous messages in the conversation history
- Look at the MOST RECENT user message - this is the representative's response to your greeting
- Understand what they said: Did they acknowledge? Ask how they can help? Ask a specific question?
- Identify where you are in the workflow (just transitioned from greeting, mid-information exchange, etc.)
- Remember: YOU are the caller, so YOU should drive the conversation forward

VERIFICATION WORKFLOW - Navigate naturally through these steps:

STEP 1: INITIAL ENGAGEMENT (Respond to their greeting response)
- If they just said "Hi" or acknowledged your greeting → Proactively start providing information: "I'm calling about {patient_name}, date of birth {dob}. I need to verify eligibility for CPT code {cpt_code}."
- If they asked "How can I help?" → Start with patient basics: "I need to verify eligibility for {patient_name}, born {dob}. The member ID is {member_id}."
- If they asked a specific question → Answer it directly from PATIENT INFORMATION
- DO NOT ask "What information do you need from me?" - YOU are driving this call

STEP 2: PROVIDE INFORMATION PROACTIVELY
- After initial engagement, continue with remaining key details unless they interrupt:
  * "The member ID is {member_id}"
  * "We're verifying CPT code {cpt_code}"
  * "The provider NPI is {provider_npi}"
- If they ask for specific information, provide it immediately from PATIENT INFORMATION
- Spell out IDs and numbers clearly
- Repeat information as many times as needed
- If asked for information NOT in PATIENT INFORMATION, say: "I don't have that information available"

STEP 3: VERIFY INSURANCE COVERAGE
- Once you've provided the information they requested, ask: "Can you confirm if this procedure is covered under their plan?"
- Listen for their response about coverage/authorization status

STEP 4: RECORD AUTHORIZATION STATUS (First Function Call)
Use the record_authorization_status function based on their response:
- If APPROVED/AUTHORIZED/COVERED → Call: record_authorization_status(status="Approved")
- If DENIED/NOT COVERED → Call: record_authorization_status(status="Denied")
- If PENDING/UNDER REVIEW → Call: record_authorization_status(status="Pending")
- This function will record the status and you'll stay in verification node

STEP 5: GET REFERENCE NUMBER (Second Function Call)
- After recording status, ask: "Could you provide a reference or authorization number for this verification?"
- Listen for their response with the reference number
- When they provide it, call: record_reference_number(reference_number="their_response")
- This function will record the number and automatically transition to confirmation node
- The confirmation node will speak both status and reference number back to the rep

SUPERVISOR TRANSFER DETECTION:
- If the representative requests to speak with a supervisor, manager, or real person
- Examples: "Can I speak to your manager?", "I need to talk to a real person", "Transfer me to a supervisor"
- Call request_supervisor function

CONVERSATION GUIDELINES:
- YOU are the caller - be proactive and drive the conversation forward
- Read the conversation history to understand context before responding
- Be natural and conversational, not robotic
- Keep individual responses under 30 words unless providing multiple data points
- Stay professional and helpful
- Don't repeat information already provided in greeting unless asked
- Answer questions directly and clearly from PATIENT INFORMATION"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="record_authorization_status",
                    description="Record the authorization status (Approved/Denied/Pending) from the insurance representative.",
                    properties={
                        "status": {
                            "type": "string",
                            "description": "Authorization status: 'Approved', 'Denied', or 'Pending'"
                        }
                    },
                    required=["status"],
                    handler=self.record_authorization_status
                ),
                FlowsFunctionSchema(
                    name="record_reference_number",
                    description="Record the reference or authorization number provided by the insurance representative.",
                    properties={
                        "reference_number": {
                            "type": "string",
                            "description": "The reference or authorization number from the insurance company"
                        }
                    },
                    required=["reference_number"],
                    handler=self.record_reference_number
                ),
                FlowsFunctionSchema(
                    name="request_supervisor",
                    description="Request to speak with a supervisor when representative asks for transfer.",
                    properties={},
                    required=[],
                    handler=self._request_supervisor_handler
                ),
                FlowsFunctionSchema(
                    name="proceed_to_closing",
                    description="Transition to closing node after verification is complete.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_closing_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.APPEND
            )
        )

    def create_supervisor_confirmation_node(self) -> NodeConfig:
        facility = self.patient_data.get('facility_name')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="supervisor_confirmation",
            role_messages=[{
                "role": "system",
                "content": f"""You are Alexandra, a Virtual Assistant from {facility}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": """The representative has requested to speak with a supervisor.

1. Ask: "Would you like to speak with my supervisor?"
2. Wait for their response

IF THEY CONFIRM (yes, sure, please):
   - Call dial_supervisor() function
   - The function will handle the transfer and goodbye message

IF THEY DECLINE (no, nevermind):
   - Say: "No problem, let's continue."
   - Call return_to_verification()"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="dial_supervisor",
                    description="Execute supervisor transfer via SIP call transfer.",
                    properties={},
                    required=[],
                    handler=self._dial_supervisor_handler
                ),
                FlowsFunctionSchema(
                    name="return_to_verification",
                    description="Return to verification node from supervisor confirmation.",
                    properties={},
                    required=[],
                    handler=self._return_to_verification_handler
                )
            ],
            respond_immediately=True
        )

    def create_authorization_confirmation_node(self) -> NodeConfig:
        """Node for confirming authorization status and reference number back to the rep.

        This node speaks immediately upon entry, confirming the recorded information
        with proper speech formatting for the reference number.
        """
        facility = self.patient_data.get('facility_name')
        global_instructions = self._get_global_instructions()

        # Get the values that were just recorded
        status = self.patient_data.get('prior_auth_status', 'Unknown')
        ref_number = self.patient_data.get('reference_number', '')

        # Format reference number for speech
        ref_number_speech = self._format_reference_number(ref_number) if ref_number else ""

        return NodeConfig(
            name="authorization_confirmation",
            role_messages=[{
                "role": "system",
                "content": f"""You are Alexandra, a Virtual Assistant from {facility}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You just recorded the authorization information in the database. Now confirm it back to the representative for verification.

RECORDED INFORMATION:
- Authorization Status: {status}
- Reference Number: {ref_number}

PRONUNCIATION FOR REFERENCE NUMBER:
- Use this formatted version when speaking: {ref_number_speech}

YOUR TASK:
1. Confirm both pieces of information back to them clearly and professionally
2. Structure your response like: "Perfect, I have that recorded. The authorization status is {status}, and the reference number is [speak the formatted version]."
3. After confirming, immediately call proceed_to_closing() to wrap up the call

CRITICAL RULES:
- Use the formatted reference number version for clear pronunciation
- Keep the confirmation concise (under 25 words)
- Stay professional and helpful
- Do NOT ask if they need anything else - that's the closing node's job
- After speaking the confirmation, call the function to proceed"""
            }],
            functions=[FlowsFunctionSchema(
                name="proceed_to_closing",
                description="Transition to closing node after confirmation is complete.",
                properties={},
                required=[],
                handler=self._proceed_to_closing_handler
            )],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_closing_node(self) -> NodeConfig:
        patient_name = self.patient_data.get('patient_name')
        facility = self.patient_data.get('facility_name')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="closing",
            role_messages=[{
                "role": "system",
                "content": f"""You are Alexandra, a Virtual Assistant from {facility} concluding the call.
PATIENT: {patient_name}

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": """You have completed the main verification. Now check if the representative needs anything else.

WORKFLOW:

1. FIRST RESPONSE (when entering closing state):
   Ask: "Thank you for your help today. Is there anything else I can provide?"
   Wait for their response

2. EVALUATE THEIR RESPONSE:

   A. IF THEY SAY NO / ALL SET / NOTHING ELSE / THAT'S IT:
      - Say something like: "Great, then! Have a great day, Goodbye!"
      - Immediately call: end_call()

   B. IF THEY HAVE ADDITIONAL QUESTIONS OR REQUESTS:
      Examples:
      - "Can you confirm the member ID again?"
      - "Actually, let me verify the CPT code"

      Action: Call return_to_verification() to handle their request

3. ANSWER ANY QUESTIONS:
   - You can answer questions about ANY patient information fields at ANY time
   - Use exact values from PATIENT INFORMATION section
   - If they ask for information you don't have, say "I don't have that information available"

IMPORTANT:
- Keep responses under 15 words
- Stay professional and helpful
- Only say the final goodbye phrase when they confirm nothing else is needed"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="return_to_verification",
                    description="Return to verification node from closing state.",
                    properties={},
                    required=[],
                    handler=self._return_to_verification_handler
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the conversation and terminate the call.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler
                )
            ],
            respond_immediately=True
        )

    async def _switch_to_classifier_llm(self, action: dict, flow_manager: FlowManager):
        await self.context_aggregator.assistant().push_frame(
            ManuallySwitchServiceFrame(service=self.classifier_llm),
            FrameDirection.UPSTREAM
        )
        logger.info("✅ LLM: classifier (fast greeting)")

    async def _switch_to_main_llm(self, action: dict, flow_manager: FlowManager):
        """Pre-action: Switch to main_llm before node starts.

        Used by verification node to enable function calling capabilities.
        """
        await self.context_aggregator.assistant().push_frame(
            ManuallySwitchServiceFrame(service=self.main_llm),
            FrameDirection.UPSTREAM
        )
        logger.info("✅ LLM: main (function calling)")

    async def _proceed_to_verification_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, 'NodeConfig']:
        """
        Handler for proceed_to_verification function.

        This function moves the conversation forward after the initial greeting,
        transitioning to the verification node where patient information collection begins.

        Args:
            args: Function arguments (may be None/empty dict).
            flow_manager: The flow manager instance controlling conversation flow.

        Returns:
            tuple[None, NodeConfig]: Returns (None, verification_node_config).
        """
        logger.info("✅ Flow: greeting → verification")
        return None, self.create_verification_node()

    async def record_authorization_status(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """
        Record authorization status (step 1 of 2-step workflow).

        This function stores the authorization decision (Approved/Denied/Pending) in the
        MongoDB database. After recording, it stays in the verification node to allow
        the LLM to ask for the reference number.

        Args:
            args (Dict[str, Any]): Function arguments containing 'status' key.
            flow_manager (FlowManager): The flow manager instance controlling conversation flow.

        Returns:
            tuple[str, None]: Returns (result_message, None).
                - str: Confirmation message for LLM context (e.g., "Recorded status: Approved").
                - None: Stays in current verification node to collect reference number.
        """
        try:
            status = args['status']
            patient_id = self.patient_data.get('patient_id')
            if not patient_id:
                logger.error("❌ No patient_id found in patient_data")
                return "Error: No patient ID available", None

            db = get_async_patient_db()
            update_fields = {'prior_auth_status': status}
            await db.update_patient(patient_id, update_fields)

            # Update self.patient_data for later reference
            self.patient_data['prior_auth_status'] = status

            logger.info(f"✅ Authorization status recorded: {status}")
            return f"Recorded status: {status}. Now ask for reference number.", None

        except Exception as e:
            import traceback
            logger.error(f"❌ Failed to record authorization status: {traceback.format_exc()}")
            return f"Error recording status: {str(e)}", None

    async def record_reference_number(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, 'NodeConfig']:
        """
        Record reference/authorization number (step 2 of 2-step workflow).

        This function stores the reference number provided by the insurance representative
        in the MongoDB database. After successfully recording, it transitions to the
        authorization_confirmation node where the bot will speak both the status and
        reference number back to the representative for verification.

        Args:
            args (Dict[str, Any]): Function arguments containing 'reference_number' key.
            flow_manager (FlowManager): The flow manager instance controlling conversation flow.

        Returns:
            tuple[str, NodeConfig]: Returns (result_message, next_node).
                - str: Confirmation message for LLM context (e.g., "Recorded reference number").
                - NodeConfig: authorization_confirmation node for confirming details back to rep.
        """
        try:
            reference_number = args['reference_number']
            patient_id = self.patient_data.get('patient_id')
            if not patient_id:
                logger.error("❌ No patient_id found in patient_data")
                return "Error: No patient ID available", None

            db = get_async_patient_db()
            update_fields = {'reference_number': reference_number}
            await db.update_patient(patient_id, update_fields)

            # Update self.patient_data so confirmation node has latest values
            self.patient_data['reference_number'] = reference_number

            logger.info(f"✅ Reference number recorded: {reference_number}")
            logger.info("✅ Flow: verification → authorization_confirmation")

            # Transition to confirmation node to speak details back to rep
            return "Recorded reference number", self.create_authorization_confirmation_node()

        except Exception as e:
            import traceback
            logger.error(f"❌ Failed to record reference number: {traceback.format_exc()}")
            return f"Error recording reference number: {str(e)}", None

    async def _request_supervisor_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, 'NodeConfig']:
        """
        Handler for request_supervisor function.

        This function transitions the conversation to the supervisor confirmation node,
        where the LLM will confirm the transfer request before executing it.

        Args:
            args: Function arguments (may be None/empty dict).
            flow_manager: The flow manager instance controlling conversation flow.

        Returns:
            tuple[None, NodeConfig]: Returns (None, supervisor_confirmation_node_config).
        """
        logger.info("✅ Flow: verification → supervisor_confirmation")
        return None, self.create_supervisor_confirmation_node()

    async def _dial_supervisor_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, None]:
        """
        Handler for dial_supervisor function.

        This function performs a cold transfer to the configured supervisor phone number.
        It validates the phone format, speaks a goodbye message, then initiates the SIP
        transfer. The bot will exit when the supervisor answers.

        Args:
            args: Function arguments (may be None/empty dict).
            flow_manager: The flow manager instance controlling conversation flow.

        Returns:
            tuple[str, None]: Returns (result_message, None).
        """
        supervisor_phone = self.patient_data.get("supervisor_phone")

        if not supervisor_phone:
            error_msg = {
                "role": "system",
                "content": "I apologize, I don't have a supervisor number available. Let's continue with the verification."
            }
            await self.context_aggregator.assistant().push_frame(
                LLMMessagesAppendFrame([error_msg], run_llm=True)
            )
            return "No supervisor available", None

        import re
        if not re.match(r'^\+\d{10,15}$', supervisor_phone):
            logger.error(f"❌ Invalid supervisor phone format: {supervisor_phone}")
            error_msg = {
                "role": "system",
                "content": "I apologize, the supervisor phone number is not configured correctly. Let's continue."
            }
            await self.context_aggregator.assistant().push_frame(
                LLMMessagesAppendFrame([error_msg], run_llm=True)
            )
            return "Invalid phone format", None

        try:
            # Update patient status
            patient_id = self.patient_data.get('patient_id')
            if patient_id:
                db = get_async_patient_db()
                await db.update_call_status(patient_id, 'Supervisor Requested')

            # Add to transcript
            if self.pipeline and hasattr(self.pipeline, 'transcripts'):
                from datetime import datetime
                self.pipeline.transcripts.append({
                    "role": "system",
                    "content": "Supervisor Transfer Initiated",
                    "timestamp": datetime.now().isoformat(),
                    "type": "system_event"
                })

            # Speak goodbye BEFORE transferring (critical for cold transfer)
            goodbye_msg = {
                "role": "system",
                "content": "I'm transferring you to my supervisor now. Please hold while I connect you. Goodbye."
            }
            await self.context_aggregator.assistant().push_frame(
                LLMMessagesAppendFrame([goodbye_msg], run_llm=True)
            )

            # Set transfer flag before initiating transfer
            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            # Initiate SIP transfer
            if self.transport:
                transfer_params = {"toEndPoint": supervisor_phone}
                await self.transport.sip_call_transfer(transfer_params)
                logger.info(f"✅ Supervisor transfer initiated: {supervisor_phone}")

            # Bot will exit when supervisor answers (via on_dialout_answered handler)
            return "Transfer initiated", None

        except Exception as e:
            import traceback
            logger.error(f"❌ Transfer failed: {traceback.format_exc()}")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            error_msg = {
                "role": "system",
                "content": "I apologize, the transfer failed. Let's continue with the verification."
            }
            await self.context_aggregator.assistant().push_frame(
                LLMMessagesAppendFrame([error_msg], run_llm=True)
            )
            return "Transfer failed", None

    async def _return_to_verification_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, 'NodeConfig']:
        """
        Handler for return_to_verification function.

        This function allows the conversation to loop back to the verification node,
        useful when the representative indicates they need to provide more information
        after reaching the closing node.

        Args:
            args: Function arguments (may be None/empty dict).
            flow_manager: The flow manager instance controlling conversation flow.

        Returns:
            tuple[None, NodeConfig]: Returns (None, verification_node_config).
        """
        logger.info("✅ Flow: returning to verification")
        return None, self.create_verification_node()

    async def _proceed_to_closing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, 'NodeConfig']:
        """
        Handler for proceed_to_closing function.

        This function moves the conversation to the closing state after verification is complete,
        where the LLM will ask if the representative needs anything else before ending the call.

        Args:
            args: Function arguments (may be None/empty dict).
            flow_manager: The flow manager instance controlling conversation flow.

        Returns:
            tuple[None, NodeConfig]: Returns (None, closing_node_config).
        """
        logger.info("✅ Flow: verification → closing")
        return None, self.create_closing_node()

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, None]:
        """
        Handler for end_call function.

        This function gracefully ends the call by queueing an EndFrame
        to signal the pipeline to terminate.

        Args:
            args: Function arguments (may be None/empty dict).
            flow_manager: The flow manager instance controlling conversation flow.

        Returns:
            tuple[None, None]: Returns (None, None).
        """
        from pipecat.frames.frames import EndFrame
        logger.info("✅ Call ended by flow")
        if self.pipeline:
            await self.pipeline.task.queue_frames([EndFrame()])
        return None, None
