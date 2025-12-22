import os
from typing import Any, Dict

from openai import AsyncOpenAI
from pipecat_flows import (
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)
from loguru import logger

from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


async def warmup_openai(organization_name: str = "Demo Clinic Alpha"):
    """Warm up OpenAI with system prompt prefix for cache hits.

    OpenAI caches prompt prefixes of 1024+ tokens. We send a request
    with the same system prompt structure to prime the cache.
    """
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        global_instructions = f"""You are Monica, answering the main phone line for {organization_name}.

# Your Role
You answer simple questions directly and route complex issues to the right place.
Be helpful, friendly, and efficient. Most callers just need quick information.

# Questions You CAN Answer Directly
- Office hours: Monday through Friday, 8 AM to 5 PM
- Location: Contact the front desk for address
- Parking: Available on-site
- New patients: We are accepting new patients

# When to Route (do NOT answer these yourself)
- SCHEDULING: "schedule/book/cancel/reschedule appointment" → route to scheduling
- LAB RESULTS: "lab results/test results/biopsy/pathology" → route to lab_results
- PRESCRIPTIONS: "prescription/refill/medication" → route to prescription_status
- BILLING: "bill/payment/insurance claim/costs" → route to billing
- UNCLEAR or COMPLEX: Anything you're not sure about → route to front_desk

# Voice Conversation Style
You are having a real-time phone conversation. Your responses will be converted to speech:
- Speak naturally like a human would on the phone
- Keep responses short and direct. One or two sentences max.
- NEVER use bullet points, numbered lists, asterisks, or markdown
- Say "Got it" or "Sure thing" instead of formal phrases
- Use natural filler when appropriate: "Let me see..." or "Okay, so..."

# Handling Speech Recognition
The input you receive is transcribed from speech and may contain errors:
- Silently correct obvious transcription mistakes based on context
- If truly unclear, ask them to repeat naturally: "Sorry, I didn't catch that"

# Guardrails
- If the caller is frustrated or asks for a human: route to front_desk immediately
- Never guess at specific information like appointment availability or account details
- Keep the conversation moving - don't over-explain"""

        task_context = """# Goal
Determine what the caller needs and route appropriately. This step is important.

# Scenario Handling
If caller asks a SIMPLE QUESTION (hours, location, parking):
→ Answer directly, then ask "Is there anything else I can help with?"

If caller needs SCHEDULING (appointments):
→ Call route_to_workflow with workflow="scheduling"

If caller needs LAB RESULTS:
→ Call route_to_workflow with workflow="lab_results"

If caller needs PRESCRIPTION help:
→ Call route_to_workflow with workflow="prescription_status"

If caller needs BILLING or asks for a HUMAN:
→ Call route_to_staff with appropriate department"""

        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": global_instructions},
                {"role": "system", "content": task_context},
                {"role": "user", "content": "Hi, I'm calling to check on my lab results"},
                {"role": "assistant", "content": "I can help you with that. Let me connect you to our lab results line."},
            ],
            max_tokens=1,
        )
        logger.info("OpenAI connection warmed up with mainline prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class MainlineFlow:
    """Main phone line - answer patient questions or route to appropriate workflow/department.

    Routes to AI workflows (same call, no transfer):
    - scheduling → PatientSchedulingFlow
    - lab_results → LabResultsFlow
    - prescription_status → PrescriptionStatusFlow

    Routes via SIP transfer (cold transfer to phone number):
    - billing → Billing department phone
    - front_desk → Front desk phone (fallback)
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

        # Practice info from schema/organization config
        practice_info = patient_data.get("practice_info", {})
        self.office_hours = practice_info.get("office_hours", "Monday through Friday, 8 AM to 5 PM")
        self.location = practice_info.get("location", "")
        self.parking = practice_info.get("parking", "")
        self.website = practice_info.get("website", "")
        self.new_patient_info = practice_info.get("new_patient_info", "")
        self.accepted_insurance = practice_info.get("accepted_insurance", "")
        self.wait_times = practice_info.get("wait_times", "")

        # Initialize flow state
        self._init_state()

    def _init_state(self):
        """Initialize flow_manager state with default values."""
        # Call tracking
        self.flow_manager.state["caller_name"] = ""
        self.flow_manager.state["call_type"] = ""
        self.flow_manager.state["call_reason"] = ""
        self.flow_manager.state["routed_to"] = ""
        self.flow_manager.state["resolution"] = ""

        # For retry transfer
        self.flow_manager.state["pending_department"] = ""

    def _get_global_instructions(self) -> str:
        """Global behavioral rules for main line interactions."""
        # Build practice info section from configured values
        practice_facts = []
        if self.office_hours:
            practice_facts.append(f"- Office hours: {self.office_hours}")
        if self.location:
            practice_facts.append(f"- Location: {self.location}")
        if self.parking:
            practice_facts.append(f"- Parking: {self.parking}")
        if self.new_patient_info:
            practice_facts.append(f"- New patient info: {self.new_patient_info}")
        if self.wait_times:
            practice_facts.append(f"- General wait times: {self.wait_times}")
        if self.website:
            practice_facts.append(f"- Website: {self.website}")
        if self.accepted_insurance:
            practice_facts.append(f"- Accepted insurance: {self.accepted_insurance}")

        practice_info_text = "\n".join(practice_facts) if practice_facts else "- Contact the front desk for practice information"

        return f"""You are Monica, answering the main phone line for {self.organization_name}.

# Your Role
You answer simple questions directly and route complex issues to the right place.
Be helpful, friendly, and efficient. Most callers just need quick information.

# Questions You CAN Answer Directly
{practice_info_text}

# When to Route (do NOT answer these yourself)
- SCHEDULING: "schedule/book/cancel/reschedule appointment" → route to scheduling
- LAB RESULTS: "lab results/test results/biopsy/pathology" → route to lab_results
- PRESCRIPTIONS: "prescription/refill/medication" → route to prescription_status
- BILLING: "bill/payment/insurance claim/costs" → route to billing
- CHECK-IN: "check in/arrived/here for appointment" → route to front_desk immediately
- UNCLEAR or COMPLEX: Anything you're not sure about → route to front_desk

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
- If the caller is frustrated or asks for a human: route to front_desk immediately
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
                    "content": """# Goal
Determine what the caller needs and route appropriately. Gather context before routing so the next workflow has the information it needs.

# Scenario Handling

If caller asks a SIMPLE QUESTION (hours, location, parking, new patient info):
→ Answer directly in a friendly way
→ Ask "Is there anything else I can help with?"
→ If they say no/goodbye, call end_call

If caller needs SCHEDULING (book, cancel, reschedule appointment):
→ First, gather context with ONE quick question if not already provided:
  - "What would the appointment be for?" (if reason not mentioned)
  - OR if they mentioned a reason, acknowledge it and route
→ Include all context in the reason field when routing
→ Call route_to_workflow with workflow="scheduling"

If caller needs LAB RESULTS (test results, biopsy, pathology):
→ Acknowledge their concern: "I can help you check on that."
→ Ask ONE clarifying question if needed: "What type of test was it?" or "When was it done?"
→ Note any urgency or anxiety they express
→ Call route_to_workflow with workflow="lab_results", include test type and urgency in reason

If caller needs PRESCRIPTION (refill, medication status, pharmacy issues):
→ Say "I can look into that for you."
→ Ask which medication if not mentioned
→ Note any complications they mention (pharmacy issues, prior auth, etc.)
→ Call route_to_workflow with workflow="prescription_status", include details in reason
→ NOTE: Use route_to_workflow, NOT route_to_staff for prescriptions

If caller needs BILLING (payment, bill question, insurance claim, costs):
→ Say "Let me get you to our billing team."
→ Call request_staff with department="billing"

If caller needs to CHECK IN for an existing appointment:
→ Say "Let me connect you with the front desk for check-in."
→ Call request_staff with department="front_desk", reason="check-in for appointment"
→ NOTE: Check-in is NOT scheduling - transfer immediately, don't ask questions

If caller ASKS FOR HUMAN (says "real person", "someone", "transfer me"):
→ Say "Let me transfer you to someone who can help."
→ Call request_staff with department="front_desk"

If TRULY UNCLEAR (you genuinely don't understand what they need):
→ Ask one clarifying question first
→ Only transfer if still unclear after that

NOTE: Multiple intents is NOT "complex" - handle them in sequence (e.g., lab results first, then scheduling)

# Context Gathering Examples

Caller: "I need to schedule a follow-up."
→ "Sure! What's the follow-up for?"
Caller: "My back pain. I've been seeing Dr. Chen."
→ "Got it, a follow-up for back pain with Dr. Chen."
→ Call route_to_workflow with workflow="scheduling", reason="follow-up for back pain, prefers Dr. Chen, returning patient"

Caller: "I'm calling about some test results. Had a biopsy last week."
→ "I can help you check on that. Was that a skin biopsy, or something else?"
Caller: "Skin biopsy. I'm pretty worried about it."
→ "I understand. Let me get you to someone who can look that up."
→ Call route_to_workflow with workflow="lab_results", reason="skin biopsy from last week, caller is anxious about results"

# Guardrails
- Gather context naturally, don't interrogate. ONE question max before routing.
- NEVER repeat a question you already asked - even if the caller changes their mind and comes back to the same topic. Track what was discussed:
  - If you already asked about test type → don't ask again, use "blood work" or whatever they said
  - If you already asked about appointment reason → don't ask again, use what they mentioned
  - If caller says "I already told you" or repeats info → acknowledge and move forward
- Be conversational. Don't announce what you're about to do.
- Never guess at specific information like appointment availability or account details.
- If caller is frustrated or asks for a human, route to front_desk immediately.
- Include ALL mentioned details in the reason field - this context transfers to the next workflow.

# Error Handling
If you don't understand the caller:
→ Ask naturally: "I'm sorry, could you repeat that?"
→ Never guess what they need""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="route_to_workflow",
                    description="""Route caller to an AI-powered workflow.

WHEN TO USE: Caller asks about scheduling, lab results, or prescriptions.
RESULT: Hands off to specialized AI workflow (no phone transfer).

IMPORTANT:
- Include ALL gathered context in the reason field
- Do NOT say "someone will be with you", "I've routed your request", or "please hold" - the transition is seamless
- The next workflow will speak directly to the caller

EXAMPLES:
- workflow="scheduling", reason="follow-up for back pain, prefers Dr. Chen, returning patient"
- workflow="lab_results", reason="skin biopsy from last week, caller anxious, results overdue"
- workflow="prescription_status", reason="lisinopril 10mg refill, CVS says prior auth needed" """,
                    properties={
                        "workflow": {
                            "type": "string",
                            "enum": ["scheduling", "lab_results", "prescription_status"],
                            "description": "Workflow: scheduling (appointments), lab_results (test/biopsy results), prescription_status (refills/medications)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Detailed context: include appointment type, doctor preference, test type, urgency, complications - everything the next workflow needs",
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

IMPORTANT: Always include a reason, even for direct transfer requests.

EXAMPLES:
- "I have a question about my bill" → department="billing", reason="billing question"
- "Can I speak to someone?" → department="front_desk", patient_confirmed=true, reason="caller requested human"
- Urgent/frustrated caller → department="front_desk", urgent=true, reason="caller frustrated"
- Unclear request → department="front_desk", reason="unclear request" """,
                    properties={
                        "urgent": {
                            "type": "boolean",
                            "description": "Set true for urgent requests that need immediate attention (frustrated caller, medical concerns). Transfers immediately.",
                        },
                        "patient_confirmed": {
                            "type": "boolean",
                            "description": "Set true if caller explicitly asked for human/staff transfer. Transfers immediately.",
                        },
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
                    name="save_call_info",
                    description="""Save information the caller volunteers.

WHEN TO USE: Caller provides their name or explains why they're calling.
RESULT: Stores info in state for later use/logging.

EXAMPLES:
- "This is John Smith calling" → caller_name="John Smith"
- "I'm calling about my appointment" → call_reason="appointment inquiry" """,
                    properties={
                        "caller_name": {
                            "type": "string",
                            "description": "Caller's name if they provide it",
                        },
                        "call_reason": {
                            "type": "string",
                            "description": "Brief summary of why they called",
                        },
                    },
                    required=[],
                    handler=self._save_call_info_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call gracefully.

WHEN TO USE: Caller says goodbye, thanks you, or confirms no more questions.
RESULT: Transitions to goodbye message and ends conversation.

EXAMPLES:
- "That's all, thank you" → call end_call
- "Bye!" → call end_call
- "No, I'm good" (after asking if anything else) → call end_call""",
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

    def create_transfer_initiated_node(self) -> NodeConfig:
        """Node shown while SIP transfer is in progress."""
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
        """Node shown when SIP transfer fails."""
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
                    "content": """# Goal
The transfer didn't go through. Apologize and offer alternatives. This step is important.

# Scenario Handling
If caller wants to try the transfer again:
→ Call retry_transfer

If caller has a question you can answer (hours, location, parking):
→ Answer it directly
→ Ask "Is there anything else I can help with?"

If caller says goodbye or wants to end call:
→ Call end_call

# Example Flow
You: "I'm sorry, that transfer didn't connect. Is there something else I can help you with, or would you like to try again?"
Caller: "Can you try again?"
→ Call retry_transfer

Caller: "What are your hours?"
→ "We're open Monday through Friday, 8 AM to 5 PM. Anything else?"

# Guardrails
- Apologize briefly but don't over-apologize
- Offer concrete alternatives
- If caller is frustrated, offer to try again or take a message

# Error Handling
If you don't understand the caller:
→ Ask naturally: "I'm sorry, could you repeat that?" """,
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="retry_transfer",
                    description="""Retry the failed transfer.

WHEN TO USE: Caller wants to try the transfer again.
RESULT: Attempts SIP transfer to the same department.

EXAMPLES:
- "Yes, please try again" → call retry_transfer
- "Can you transfer me again?" → call retry_transfer""",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="""End the call gracefully.

WHEN TO USE: Caller says goodbye or confirms no more questions.
RESULT: Transitions to goodbye message and ends conversation.

EXAMPLES:
- "That's okay, I'll call back" → call end_call
- "No thanks, bye" → call end_call""",
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

        if caller_name:
            flow_manager.state["caller_name"] = caller_name
        if call_reason:
            flow_manager.state["call_reason"] = call_reason

        logger.info(f"Flow: Saved call info - name={caller_name}, reason={call_reason}")

        # Stay in current node
        return None, None

    async def _route_to_workflow_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Route to an AI workflow (same call, no phone transfer)."""
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")

        flow_manager.state["call_type"] = workflow.replace("_", " ").title()
        flow_manager.state["call_reason"] = reason
        flow_manager.state["routed_to"] = f"{workflow} (AI)"

        logger.info(f"Flow: Routing to {workflow} workflow - reason: {reason}")

        # ========== WORKFLOW HANDOFFS ==========
        # Each workflow handoff creates a new flow instance with the same
        # flow_manager (preserving state) and returns a node from that flow.

        if workflow == "scheduling":
            return await self._handoff_to_scheduling(flow_manager)

        elif workflow == "lab_results":
            return await self._handoff_to_lab_results(flow_manager)

        elif workflow == "prescription_status":
            return await self._handoff_to_prescription_status(flow_manager)

        else:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

    async def _handoff_to_scheduling(
        self, flow_manager: FlowManager
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

        # Get context from mainline conversation
        context = flow_manager.state.get("call_reason", "")
        logger.info(f"Flow: Handing off to PatientSchedulingFlow with context: {context}")

        # Use handoff entry point with context (no greeting, context-aware)
        return None, scheduling_flow.create_handoff_entry_node(context=context)

    async def _handoff_to_lab_results(
        self, flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Hand off to LabResultsFlow with gathered context."""
        from clients.demo_clinic_alpha.lab_results.flow_definition import LabResultsFlow

        lab_results_flow = LabResultsFlow(
            patient_data=self.patient_data,
            flow_manager=flow_manager,
            main_llm=self.main_llm,
            context_aggregator=self.context_aggregator,
            transport=self.transport,
            pipeline=self.pipeline,
            organization_id=self.organization_id,
            cold_transfer_config=self.cold_transfer_config,
        )

        # Get context from mainline conversation
        context = flow_manager.state.get("call_reason", "")
        logger.info(f"Flow: Handing off to LabResultsFlow with context: {context}")

        # Use handoff entry point with context (no greeting, context-aware)
        return None, lab_results_flow.create_handoff_entry_node(context=context)

    async def _handoff_to_prescription_status(
        self, flow_manager: FlowManager
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

        # Get context from mainline conversation
        context = flow_manager.state.get("call_reason", "")
        logger.info(f"Flow: Handing off to PrescriptionStatusFlow with context: {context}")

        # Use handoff entry point with context (no greeting, context-aware)
        return None, prescription_flow.create_handoff_entry_node(context=context)

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Route to human staff via SIP transfer."""
        urgent = args.get("urgent", False)
        patient_confirmed = args.get("patient_confirmed", False)
        department = args.get("department", "front_desk")
        reason = args.get("reason", "")

        flow_manager.state["call_type"] = department.replace("_", " ").title()
        flow_manager.state["call_reason"] = reason
        flow_manager.state["routed_to"] = f"{department.replace('_', ' ').title()} (staff)"
        flow_manager.state["transfer_reason"] = reason

        logger.info(f"Flow: Routing to {department} staff - reason: {reason}, urgent: {urgent}, confirmed: {patient_confirmed}")

        return await self._initiate_sip_transfer(flow_manager, department)

    async def _initiate_sip_transfer(
        self, flow_manager: FlowManager, department: str
    ) -> tuple[None, NodeConfig]:
        """Initiate SIP transfer to a phone number."""
        # Store department for retry attempts
        flow_manager.state["pending_department"] = department

        # Map department to phone number
        phone_numbers = {
            "billing": self.cold_transfer_config.get("billing_number"),
            "front_desk": self.cold_transfer_config.get("staff_number"),
            "medical": self.cold_transfer_config.get("medical_number"),
        }

        transfer_number = phone_numbers.get(department) or self.cold_transfer_config.get("staff_number")

        if not transfer_number:
            logger.warning(f"No transfer number configured for {department}")
            return None, self.create_transfer_failed_node()

        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": transfer_number})
                logger.info(f"SIP transfer initiated to {department}: {transfer_number}")

            # Update call status
            try:
                patient_id = self.patient_data.get("patient_id")
                if patient_id:
                    db = get_async_patient_db()
                    await db.update_call_status(patient_id, "Transferred", self.organization_id)
            except Exception as e:
                logger.error(f"Error updating call status: {e}")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception(f"SIP transfer to {department} failed")

            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            return None, self.create_transfer_failed_node()

    async def _retry_transfer_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Retry a failed SIP transfer."""
        department = flow_manager.state.get("pending_department", "front_desk")
        logger.info(f"Flow: Retrying SIP transfer to {department}")
        return await self._initiate_sip_transfer(flow_manager, department)

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
