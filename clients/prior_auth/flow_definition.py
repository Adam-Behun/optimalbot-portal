import logging
from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig
from pipecat.frames.frames import ManuallySwitchServiceFrame, LLMMessagesAppendFrame
from pipecat.processors.frame_processor import FrameDirection
from backend.models import AsyncPatientRecord

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

CRITICAL BEHAVIORAL RULES:
1. AI TRANSPARENCY: You are a Virtual Assistant. Disclose this in your initial greeting. Say "I'm Alexandra, a Virtual Assistant helping {facility}" when first introducing yourself. Never pretend to be human.

2. LANGUAGE: Speak ONLY in English. If the caller speaks another language, politely redirect in English: "I apologize, but I can only communicate in English for this verification."

3. TONE: Maintain a professional, courteous medical office assistant tone at ALL TIMES. Never mirror the caller's informal language, slang, accent, or emotional tone. Stay calm and professional even if the caller is frustrated, rude, or casual.

4. DATA ACCURACY: ONLY use patient information explicitly provided in the PATIENT INFORMATION section above. NEVER guess, estimate, or invent any details. If you don't have information, say "I don't have that information available" rather than making it up.

5. STAY ON TOPIC: This is an insurance verification call. Do not engage in personal conversations, medical advice, billing disputes, or any topics unrelated to insurance verification. If caller goes off-topic, politely redirect: "I appreciate that, but I'm only able to assist with the insurance verification today."

6. SPEAKING STYLE: Use complete, grammatically correct sentences. Do not use slang, informal contractions beyond standard ones, or casual phrases. Maintain consistency regardless of how the caller speaks.

7. ANSWERING QUESTIONS: You can answer questions about ANY of the patient information fields listed above at ANY time during the call, regardless of the current conversation state. Always use the exact values provided."""

    def create_greeting_node(self) -> NodeConfig:
        facility = self.patient_data.get('facility_name', 'a medical facility')
        global_instructions = self._get_global_instructions()

        return NodeConfig(
            name="greeting",
            role_messages=[{
                "role": "system",
                "content": f"""You are Alexandra, a Virtual Assistant from {facility}.

{global_instructions}"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""YOU called the insurance company representative. Greet them, disclose you're a Virtual Assistant, and provide initial patient context.

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
- Output EXACTLY ONE response containing only the greeting
- Do NOT add commentary, explanations, rephrasing, or additional sentences
- Keep it brief and professional

AFTER GREETING:
- Immediately call proceed_to_verification() to continue the conversation"""
            }],
            functions=[self.proceed_to_verification],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }],
            post_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm_post_greeting
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

FIRST: ANALYZE THE CONVERSATION CONTEXT
- Review ALL previous messages in the conversation history
- Understand what the representative has already said (greetings, questions, requests)
- Identify where you are in the workflow (just starting, mid-information exchange, etc.)
- Remember: YOU are the caller, so YOU should drive the conversation forward

VERIFICATION WORKFLOW - Navigate naturally through these steps:

STEP 1: INITIAL ENGAGEMENT
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

STEP 4: RECORD AUTHORIZATION STATUS
Use the update_prior_auth_status function based on their response:
- If APPROVED/AUTHORIZED/COVERED → Call: update_prior_auth_status with status="Approved"
- If DENIED/NOT COVERED → Call: update_prior_auth_status with status="Denied"
- If PENDING/UNDER REVIEW → Call: update_prior_auth_status with status="Pending"

STEP 5: GET REFERENCE NUMBER
- After recording status, ask: "Could you provide a reference or authorization number for this verification?"
- When they provide it, call: update_prior_auth_status with the reference_number
- After getting reference number, call proceed_to_closing to end the verification

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
                self.update_prior_auth_status,
                self.request_supervisor,
                self.proceed_to_closing
            ],
            respond_immediately=True
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
                self.dial_supervisor,
                self.return_to_verification
            ],
            respond_immediately=True
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
                self.return_to_verification,
                self.end_call
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
        await self.context_aggregator.assistant().push_frame(
            ManuallySwitchServiceFrame(service=self.main_llm),
            FrameDirection.UPSTREAM
        )
        logger.info("✅ LLM: main (function calling)")

    async def _switch_to_main_llm_post_greeting(self, action: dict, flow_manager: FlowManager):
        """Post-action: Switch to main_llm after greeting completes.

        This runs AFTER the greeting TTS completes, ensuring:
        1. The greeting is fully spoken
        2. LLM switch happens cleanly without context pollution
        """
        logger.info("✅ Post-action: Switching to main LLM")
        await self.context_aggregator.assistant().push_frame(
            ManuallySwitchServiceFrame(service=self.main_llm),
            FrameDirection.UPSTREAM
        )
        logger.info("✅ LLM: main (function calling)")

    async def proceed_to_verification(self, flow_manager: FlowManager):
        """Function: Transition from greeting to verification.

        This function is called by the LLM to progress the conversation.
        """
        logger.info("✅ Flow: greeting → verification")
        return None, self.create_verification_node()

    async def update_prior_auth_status(
        self, flow_manager: FlowManager,
        status: str,
        reference_number: str = ""
    ) -> tuple[str, None]:
        """Update patient record with authorization status.

        Args:
            status: Authorization status (Approved/Denied/Pending)
            reference_number: Reference number from insurance
        """
        patient_id = self.patient_data.get('patient_id')
        if patient_id:
            patient = AsyncPatientRecord(patient_id)
            await patient.update({
                'prior_auth_status': status,
                'reference_number': reference_number
            })
            logger.info(f"✅ Authorization recorded: {status}")

        return f"Updated status to {status}", None

    async def request_supervisor(self, flow_manager: FlowManager):
        """Transition to supervisor confirmation."""
        logger.info("✅ Flow: verification → supervisor_confirmation")
        return None, self.create_supervisor_confirmation_node()

    async def dial_supervisor(self, flow_manager: FlowManager):
        supervisor_phone = self.patient_data.get("supervisor_phone")

        if not supervisor_phone:
            error_msg = {
                "role": "system",
                "content": "I apologize, I don't have a supervisor number available. Let's continue with the verification."
            }
            await flow_manager.push_frame(LLMMessagesAppendFrame([error_msg], run_llm=True))
            return "No supervisor available", None

        import re
        if not re.match(r'^\+\d{10,15}$', supervisor_phone):
            logger.error(f"❌ Invalid supervisor phone format: {supervisor_phone}")
            error_msg = {
                "role": "system",
                "content": "I apologize, the supervisor phone number is not configured correctly. Let's continue."
            }
            await flow_manager.push_frame(LLMMessagesAppendFrame([error_msg], run_llm=True))
            return "Invalid phone format", None

        try:
            # Update patient status
            patient_id = self.patient_data.get('patient_id')
            if patient_id:
                from backend.models import AsyncPatientRecord
                patient = AsyncPatientRecord(patient_id)
                await patient.update({'call_status': 'Supervisor Requested'})

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
            await flow_manager.push_frame(LLMMessagesAppendFrame([goodbye_msg], run_llm=True))

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
            logger.error(f"❌ Transfer failed: {e}")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            error_msg = {
                "role": "system",
                "content": "I apologize, the transfer failed. Let's continue with the verification."
            }
            await flow_manager.push_frame(LLMMessagesAppendFrame([error_msg], run_llm=True))
            return "Transfer failed", None

    async def return_to_verification(self, flow_manager: FlowManager):
        """Return to verification node."""
        logger.info("✅ Flow: closing → verification")
        return None, self.create_verification_node()

    async def proceed_to_closing(self, flow_manager: FlowManager):
        """Transition to closing node."""
        logger.info("✅ Flow: verification → closing")
        return None, self.create_closing_node()

    async def end_call(self, flow_manager: FlowManager):
        """End the call."""
        logger.info("✅ Call ended by flow")
        await flow_manager.end_conversation("Thank you. Goodbye!")
        return None, None
