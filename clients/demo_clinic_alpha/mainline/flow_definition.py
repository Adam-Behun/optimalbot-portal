from typing import Any, Dict

from pipecat_flows import (
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)
from loguru import logger

from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


class MainlineFlow:
    """Main phone line - answer patient questions or route to appropriate department."""

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

        # Department routing configuration
        self.departments = {
            "scheduling": {
                "number": self.cold_transfer_config.get("scheduling_number", "+15165668219"),
                "triggers": ["schedule", "appointment", "book", "cancel", "reschedule", "available", "opening"],
            },
            "billing": {
                "number": self.cold_transfer_config.get("billing_number", "+15165668219"),
                "triggers": ["bill", "payment", "cost", "price", "insurance", "coverage", "claim"],
            },
            "medical": {
                "number": self.cold_transfer_config.get("medical_number", "+15165668219"),
                "triggers": ["prescription", "refill", "nurse", "doctor", "pain", "symptoms", "medication", "results"],
            },
            "front_desk": {
                "number": self.cold_transfer_config.get("staff_number", "+15165668219"),
                "triggers": [],  # Fallback for anything else
            },
        }

    def _get_global_instructions(self) -> str:
        """Global behavioral rules for main line interactions."""
        return f"""You are Monica, answering the main phone line for {self.organization_name}.

# Your Role
You answer simple questions directly and transfer complex issues to the right department.
Be helpful, friendly, and efficient. Most callers just need quick information.

# Questions You CAN Answer Directly
- Office hours: Monday through Friday, 8 AM to 5 PM. Saturday 9 AM to 1 PM. Closed Sundays.
- Location: 123 Main Street, Suite 100
- Parking: Free parking available in the rear lot
- New patient info: Bring photo ID and insurance card, arrive 15 minutes early
- General wait times: Usually under 15 minutes for scheduled appointments
- Website: www.democliniclpha.com
- Accepted insurance: Most major insurance plans accepted

# When to Transfer (do NOT answer these yourself)
- SCHEDULING: "I need to schedule/cancel/reschedule an appointment" → Scheduling
- BILLING: "Question about my bill/payment/insurance claim/costs" → Billing
- MEDICAL: "I need a prescription refill/have symptoms/need test results/need to speak with a nurse" → Medical
- UNCLEAR or COMPLEX: Anything you're not sure about → Front Desk

# Voice Conversation Style
You are having a real-time phone conversation. Your responses will be converted to speech, so:
- Speak naturally like a human would on the phone
- Keep responses short and direct. One or two sentences max.
- NEVER use bullet points, numbered lists, asterisks, or markdown
- Say "Got it" or "Sure thing" instead of formal phrases
- Use natural filler when appropriate: "Let me see..." or "Okay, so..."

# Handling Speech Recognition
The input you receive is transcribed from speech and may contain errors:
- Silently correct obvious transcription mistakes based on context
- If truly unclear, ask them to repeat naturally: "Sorry, I didn't catch that"

# Other Guidelines
- If the caller is frustrated or asks for a human: transfer to Front Desk immediately
- Never guess at specific information like appointment availability or account details
- Keep the conversation moving - don't over-explain"""

    # ========== Node Creation Functions ==========

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting - detect what the caller needs."""
        greeting_text = f"Hello, this is Monica at {self.organization_name}. How can I help you?"

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
                    "content": """Listen to what the caller needs and respond appropriately:

1. SIMPLE QUESTION you can answer (hours, location, parking, new patient info):
   → Answer it directly in a friendly way, then ask "Is there anything else I can help with?"
   → If they say no/goodbye, call end_call
   → If they have another question, answer it or route as needed

2. NEEDS SCHEDULING (book, cancel, reschedule appointment):
   → Say "I can transfer you to scheduling for that." and call route_to_department with department="scheduling"

3. NEEDS BILLING (payment, bill question, insurance claim, costs):
   → Say "Let me get you to our billing team." and call route_to_department with department="billing"

4. NEEDS MEDICAL (prescription, symptoms, nurse, test results, doctor):
   → Say "I'll connect you with our medical staff." and call route_to_department with department="medical"

5. UNCLEAR or COMPLEX:
   → Say "Let me transfer you to someone who can help with that." and call route_to_department with department="front_desk"

6. FRUSTRATED or ASKS FOR HUMAN:
   → Immediately call route_to_department with department="front_desk"

Be conversational. Don't announce what you're about to do, just do it naturally.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="route_to_department",
                    description="Transfer caller to appropriate department. Use when caller needs scheduling, billing, medical help, or anything you can't handle directly.",
                    properties={
                        "department": {
                            "type": "string",
                            "enum": ["scheduling", "billing", "medical", "front_desk"],
                            "description": "Department to transfer to: scheduling (appointments), billing (payments/insurance), medical (nurses/prescriptions/symptoms), front_desk (general/unclear)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for transfer, e.g., 'needs to schedule appointment', 'billing question'",
                        },
                    },
                    required=["department", "reason"],
                    handler=self._route_to_department_handler,
                ),
                FlowsFunctionSchema(
                    name="save_call_info",
                    description="Save information the caller volunteers (name, reason for calling). Call this when they provide details.",
                    properties={
                        "caller_name": {
                            "type": "string",
                            "description": "Caller's name if they provide it",
                        },
                        "call_reason": {
                            "type": "string",
                            "description": "Brief summary of why they called",
                        },
                        "call_type": {
                            "type": "string",
                            "enum": ["General Question", "Scheduling", "Billing", "Medical", "Other"],
                            "description": "Category of the call",
                        },
                    },
                    required=[],
                    handler=self._save_call_info_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the call when caller says goodbye or has no more questions.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
            ],
            respond_immediately=False,
            pre_actions=[
                {"type": "tts_say", "text": greeting_text},
            ],
        )

    def create_confirm_transfer_node(self) -> NodeConfig:
        """Confirm before transferring to department."""
        department = self.flow_manager.state.get("pending_department", "front_desk")
        department_name = {
            "scheduling": "scheduling",
            "billing": "billing",
            "medical": "our medical team",
            "front_desk": "the front desk",
        }.get(department, "the front desk")

        return NodeConfig(
            name="confirm_transfer",
            task_messages=[
                {
                    "role": "system",
                    "content": f"""You're about to transfer to {department_name}.

If caller has ALREADY confirmed (said yes, please, sure, transfer me, etc.):
→ Call confirm_transfer IMMEDIATELY. Do NOT ask again.

If they haven't confirmed yet:
→ Say "I'll transfer you to {department_name} now, okay?" and wait for response
→ If yes → call confirm_transfer
→ If no → call cancel_transfer and ask what else you can help with

ONE response max, then call the appropriate function.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="confirm_transfer",
                    description="Proceed with transfer after confirmation.",
                    properties={},
                    required=[],
                    handler=self._confirm_transfer_handler,
                ),
                FlowsFunctionSchema(
                    name="cancel_transfer",
                    description="Caller doesn't want to be transferred.",
                    properties={},
                    required=[],
                    handler=self._cancel_transfer_handler,
                ),
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
                {"type": "tts_say", "text": "Transferring you now, one moment please."}
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
                    "content": """The transfer didn't go through. Apologize briefly and offer to help another way.

Say something like: "I'm sorry, that transfer didn't connect. Is there something else I can help you with, or would you like to try again?"

- If they want to try again → call retry_transfer
- If they have a question you can answer → answer it
- If they want to end the call → call end_call""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="retry_transfer",
                    description="Try the transfer again.",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the call.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler,
                ),
            ],
            respond_immediately=True,
        )

    def _create_end_node(self) -> NodeConfig:
        """End the call with a friendly goodbye."""
        return NodeConfig(
            name="end",
            task_messages=[
                {
                    "role": "system",
                    "content": "Say a brief, friendly goodbye. Example: 'Thanks for calling! Have a great day.'",
                }
            ],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )

    # ========== Function Handlers ==========

    async def _save_call_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, None]:
        """Save caller information to state."""
        caller_name = args.get("caller_name", "").strip()
        call_reason = args.get("call_reason", "").strip()
        call_type = args.get("call_type", "").strip()

        if caller_name:
            flow_manager.state["caller_name"] = caller_name
        if call_reason:
            flow_manager.state["call_reason"] = call_reason
        if call_type:
            flow_manager.state["call_type"] = call_type

        logger.info(f"Flow: Saved call info - name={caller_name}, type={call_type}, reason={call_reason}")

        # Stay in current node
        return None, None

    async def _route_to_department_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Route to appropriate department."""
        department = args.get("department", "front_desk")
        reason = args.get("reason", "")

        flow_manager.state["pending_department"] = department
        flow_manager.state["call_type"] = department.replace("_", " ").title()
        if reason:
            flow_manager.state["call_reason"] = reason

        logger.info(f"Flow: Routing to {department} - reason: {reason}")

        # Go directly to transfer (skip confirmation for speed)
        return await self._initiate_transfer(flow_manager, department)

    async def _initiate_transfer(
        self, flow_manager: FlowManager, department: str
    ) -> tuple[None, NodeConfig]:
        """Initiate the actual transfer."""
        dept_config = self.departments.get(department, self.departments["front_desk"])
        transfer_number = dept_config["number"]

        flow_manager.state["routed_to"] = department.replace("_", " ").title()

        if not transfer_number:
            logger.warning(f"No transfer number configured for {department}")
            return None, self.create_transfer_failed_node()

        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": transfer_number})
                logger.info(f"SIP call transfer initiated to {department}: {transfer_number}")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception(f"Transfer to {department} failed")

            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            return None, self.create_transfer_failed_node()

    async def _confirm_transfer_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Proceed with confirmed transfer."""
        department = flow_manager.state.get("pending_department", "front_desk")
        return await self._initiate_transfer(flow_manager, department)

    async def _cancel_transfer_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Caller cancelled transfer, go back to greeting."""
        flow_manager.state.pop("pending_department", None)
        logger.info("Flow: Transfer cancelled, returning to greeting")
        return "No problem! What else can I help you with?", self.create_greeting_node()

    async def _retry_transfer_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Retry a failed transfer."""
        department = flow_manager.state.get("pending_department", "front_desk")
        logger.info(f"Flow: Retrying transfer to {department}")
        return await self._initiate_transfer(flow_manager, department)

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """End the call - save transcript and update status."""
        logger.info("Flow: Call ended - transitioning to end node")
        patient_id = self.patient_data.get("patient_id")
        db = get_async_patient_db() if patient_id else None

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            if patient_id and db:
                # Update with final call info
                update_fields = {
                    "call_status": "Completed",
                    "caller_name": flow_manager.state.get("caller_name"),
                    "call_type": flow_manager.state.get("call_type", "General Question"),
                    "call_reason": flow_manager.state.get("call_reason"),
                    "routed_to": flow_manager.state.get("routed_to", "Answered Directly"),
                    "resolution": flow_manager.state.get("resolution", "Call completed"),
                }
                # Filter out None values
                update_fields = {k: v for k, v in update_fields.items() if v is not None}
                await db.update_patient(patient_id, update_fields, self.organization_id)
                logger.info(f"Database status updated: Completed (patient_id: {patient_id})")

        except Exception as e:
            logger.exception("Error in end_call_handler")

            if patient_id and db:
                try:
                    await db.update_call_status(patient_id, "Failed", self.organization_id)
                except Exception as db_error:
                    logger.error(f"Failed to update status to Failed: {db_error}")

        return None, self._create_end_node()
