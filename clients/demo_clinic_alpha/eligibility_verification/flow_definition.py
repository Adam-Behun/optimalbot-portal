from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema, ContextStrategy, ContextStrategyConfig
from loguru import logger
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


class EligibilityVerificationFlow:
    """Eligibility verification flow with triage support."""

    # ═══════════════════════════════════════════════════════════════════
    # TRIAGE CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════

    TRIAGE_CLASSIFIER_PROMPT = """You are a call classification system for OUTBOUND insurance verification calls.

HUMAN CONVERSATION (respond "CONVERSATION"):
- Personal greetings: "Hello?", "Hi", "Speaking", "This is [name]"
- Department greetings: "Insurance verification, this is Sarah"
- Interactive responses: "Who is this?", "How can I help you?"
- Natural speech with pauses and informal tone

IVR SYSTEM (respond "IVR"):
- Menu options: "Press 1 for claims", "Press 2 for eligibility"
- Automated instructions: "Please enter your provider NPI"
- System prompts: "Thank you for calling [insurance company]"
- Hold messages: "Please hold while we transfer you"

VOICEMAIL SYSTEM (respond "VOICEMAIL"):
- Voicemail greetings: "You've reached the claims department, please leave a message"
- After-hours messages: "Our office is currently closed"
- Carrier messages: "The number you have dialed is not available"
- Mailbox messages: "This mailbox is full"

Output exactly one classification word: CONVERSATION, IVR, or VOICEMAIL."""

    IVR_NAVIGATION_GOAL = """Navigate to speak with a representative who can verify:
- Patient eligibility and benefits
- Prior authorization status
- CPT code coverage

Look for options like: "eligibility", "benefits", "prior authorization",
"provider services", "speak to representative", or "agent"."""

    VOICEMAIL_MESSAGE_TEMPLATE = """Hi, this is Alexandra, a virtual assistant from {facility_name},
calling about a prior authorization for {patient_name}.
Please call us back at your earliest convenience. Thank you."""

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager = None,
                 main_llm=None, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None, cold_transfer_config: Dict[str, Any] = None):
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.cold_transfer_config = cold_transfer_config or {}
        # Store patient_data for later initialization when flow_manager is set
        self._patient_data = patient_data

    def get_triage_config(self) -> dict:
        """Return triage configuration for this flow.

        Called by PipelineFactory to configure TriageDetector and IVRNavigationProcessor.
        Uses patient_data directly since flow_manager may not be set yet.
        """
        facility_name = self._patient_data.get("facility_name", "our facility")
        patient_name = self._patient_data.get("patient_name", "a patient")

        return {
            "classifier_prompt": self.TRIAGE_CLASSIFIER_PROMPT,
            "ivr_navigation_goal": self.IVR_NAVIGATION_GOAL,
            "voicemail_message": self.VOICEMAIL_MESSAGE_TEMPLATE.format(
                facility_name=facility_name,
                patient_name=patient_name,
            ),
        }

    def _init_flow_state(self):
        """Initialize flow_manager state with patient data. Called after flow_manager is set."""
        if not self.flow_manager:
            return
        self.flow_manager.state["patient_id"] = self._patient_data.get("patient_id")
        self.flow_manager.state["patient_name"] = self._patient_data.get("patient_name", "")
        self.flow_manager.state["date_of_birth"] = self._patient_data.get("date_of_birth", "")
        self.flow_manager.state["insurance_member_id"] = self._patient_data.get("insurance_member_id", "")
        self.flow_manager.state["cpt_code"] = self._patient_data.get("cpt_code", "")
        self.flow_manager.state["provider_npi"] = self._patient_data.get("provider_npi", "")
        self.flow_manager.state["provider_name"] = self._patient_data.get("provider_name", "")
        self.flow_manager.state["facility_name"] = self._patient_data.get("facility_name", "")

    def _get_global_instructions(self) -> str:
        state = self.flow_manager.state
        facility = state.get("facility_name", "")

        return f"""You are Alexandra, a Virtual Assistant from {facility}.

# Voice Conversation Style
You are making an outbound phone call. Your responses will be converted to speech, so:
- Speak naturally like a professional on the phone—use contractions and conversational flow
- Keep responses short and direct. One or two sentences is usually enough.
- Never use bullet points, numbered lists, special formatting, or markdown
- Use natural acknowledgments: "Got it" or "Perfect" instead of formal phrasing

# AI Transparency
Disclose you're a Virtual Assistant in your initial greeting. Never pretend to be human.

# Guardrails
- ONLY use patient information from Patient Data below. Never guess or invent details.
- If asked for information you don't have, say: "I don't have that information available."
- If the representative seems uncomfortable with AI, offer to transfer them to a manager.
- Stay on topic: insurance verification only. Redirect off-topic requests politely.

# Patient Data
- Patient Name: {state.get("patient_name")}
- Date of Birth: {state.get("date_of_birth")}
- Member ID: {state.get("insurance_member_id")}
- CPT Code: {state.get("cpt_code")}
- Provider NPI: {state.get("provider_npi")}
- Provider Name: {state.get("provider_name")}
- Facility: {facility}"""

    def create_greeting_node_after_ivr_completed(self) -> NodeConfig:
        facility = self.flow_manager.state.get("facility_name", "a medical facility")

        return NodeConfig(
            name="greeting_after_ivr",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": f"""A human answered after IVR navigation. Greet them professionally.

If they introduced themselves with a name → "Hi [Name], this is Alexandra from {facility}."
If no name given → "Hi, this is Alexandra from {facility}."

Then ask: "Can you help me verify eligibility and benefits for a patient?"

After speaking your greeting → call proceed_to_verification"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_verification",
                    description="Transition to verification after greeting is complete.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_verification_handler
                )
            ],
            respond_immediately=True,
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET
            )
        )

    def create_greeting_node_without_ivr(self) -> NodeConfig:
        facility = self.flow_manager.state.get("facility_name", "a medical facility")

        return NodeConfig(
            name="greeting_without_ivr",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": f"""A human answered directly. Greet them professionally.

If they introduced themselves with a name → "Hi [Name], this is Alexandra from {facility}."
If no name given → "Hi, this is Alexandra from {facility}."

Then ask: "Can you help me verify eligibility and benefits for a patient?"

After speaking your greeting → call proceed_to_verification"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_verification",
                    description="Transition to verification after greeting is complete.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_verification_handler
                )
            ],
            respond_immediately=True
        )

    def create_verification_node(self) -> NodeConfig:
        state = self.flow_manager.state
        patient_name = state.get("patient_name", "")
        dob = state.get("date_of_birth", "")
        member_id = state.get("insurance_member_id", "")
        cpt_code = state.get("cpt_code", "")
        provider_npi = state.get("provider_npi", "")

        return NodeConfig(
            name="verification",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": f"""You just greeted the representative. Now verify eligibility for {patient_name}.

Provide information proactively:
- "I'm calling about {patient_name}, date of birth {dob}."
- "The member ID is {member_id}."
- "We're verifying CPT code {cpt_code}, provider NPI {provider_npi}."

Answer their questions from Patient Data. Spell out IDs clearly when asked.

Once they confirm coverage status:
- APPROVED/AUTHORIZED → call record_authorization_status with status "Approved"
- DENIED/NOT COVERED → call record_authorization_status with status "Denied"
- PENDING/UNDER REVIEW → call record_authorization_status with status "Pending"

After recording status, ask: "Could you provide a reference number for this verification?"
When they provide it → call record_reference_number

If they ask to speak with a human or manager → call request_staff"""
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
                    name="request_staff",
                    description="Call when representative asks to speak with a human or manager.",
                    properties={},
                    required=[],
                    handler=self._request_staff_handler
                )
            ],
            respond_immediately=False
        )

    def create_staff_confirmation_node(self) -> NodeConfig:
        return NodeConfig(
            name="staff_confirmation",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": """You just asked if they'd like to speak with your manager.

- If yes/sure/please/okay → call dial_staff
- If no/nevermind/continue → call return_to_verification"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="dial_staff",
                    description="Transfer to manager when they confirm.",
                    properties={},
                    required=[],
                    handler=self._dial_staff_handler
                ),
                FlowsFunctionSchema(
                    name="return_to_verification",
                    description="Return to verification if they decline transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_verification_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "tts_say",
                "text": "Would you like to speak with my manager?"
            }]
        )

    def create_transfer_initiated_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_initiated",
            task_messages=[],
            functions=[],
            pre_actions=[{
                "type": "tts_say",
                "text": "Transferring you now, please hold."
            }],
            post_actions=[{
                "type": "end_conversation"
            }]
        )

    def create_transfer_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_failed",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": "The transfer failed. Apologize and continue helping them."
            }],
            functions=[
                FlowsFunctionSchema(
                    name="continue_verification",
                    description="Continue with verification after failed transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_verification_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "tts_say",
                "text": "I apologize, the transfer didn't go through."
            }]
        )

    def create_authorization_confirmation_node(self) -> NodeConfig:
        state = self.flow_manager.state
        status = state.get("prior_auth_status", "Unknown")
        ref_number = state.get("reference_number", "")

        return NodeConfig(
            name="authorization_confirmation",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": f"""Confirm the recorded information:
"I have that recorded. The authorization status is {status}, reference number {ref_number}. Is there anything else I can help with?"

- If no/all set → say "Great! Have a wonderful day. Goodbye!" then call end_call
- If they ask to repeat info → answer from Patient Data, then ask again
- If they need more verification → call return_to_verification"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="return_to_verification",
                    description="Return to verification node for complex follow-up questions.",
                    properties={},
                    required=[],
                    handler=self._return_to_verification_handler
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the conversation after saying goodbye.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler
                )
            ],
            respond_immediately=True
        )

    async def _proceed_to_verification_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.info("Flow: greeting → verification")
        return None, self.create_verification_node()

    async def record_authorization_status(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        try:
            status = args["status"]
            patient_id = flow_manager.state.get("patient_id")
            flow_manager.state["prior_auth_status"] = status

            if patient_id:
                db = get_async_patient_db()
                await db.update_field(patient_id, "prior_auth_status", status, self.organization_id)

            return f"Recorded status: {status}. Now ask for reference number.", None
        except Exception as e:
            logger.error(f"Failed to record authorization status: {e}")
            return f"Error recording status: {str(e)}", None

    async def record_reference_number(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, "NodeConfig"]:
        try:
            reference_number = args["reference_number"]
            patient_id = flow_manager.state.get("patient_id")
            flow_manager.state["reference_number"] = reference_number

            if patient_id:
                db = get_async_patient_db()
                await db.update_field(patient_id, "reference_number", reference_number, self.organization_id)

            return "Recorded reference number", self.create_authorization_confirmation_node()
        except Exception as e:
            logger.error(f"Failed to record reference number: {e}")
            return f"Error recording reference number: {str(e)}", None

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.info("Flow: verification → staff_confirmation")
        return None, self.create_staff_confirmation_node()

    async def _dial_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        staff_number = self.cold_transfer_config.get("staff_number")

        if not staff_number:
            logger.warning("Cold transfer requested but no staff_number configured")
            return None, self.create_transfer_failed_node()

        try:
            logger.info(f"Cold transfer initiated to: {staff_number}")

            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"SIP call transfer initiated: {staff_number}")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception("Cold transfer failed")

            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            return None, self.create_transfer_failed_node()

    async def _return_to_verification_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.info("Flow: returning to verification")
        return None, self.create_verification_node()

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, None]:
        from pipecat.frames.frames import EndTaskFrame
        from pipecat.processors.frame_processor import FrameDirection

        logger.info("Call ended by flow")

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            patient_id = flow_manager.state.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                await db.update_call_status(patient_id, "Completed", self.organization_id)
                logger.info("Database: call_status = Completed")

            if self.context_aggregator:
                await self.context_aggregator.assistant().push_frame(
                    EndTaskFrame(), FrameDirection.UPSTREAM
                )

        except Exception as e:
            logger.error(f"Error in end_call_handler: {e}")

        return None, None
