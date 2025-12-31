import os
from datetime import datetime, timezone
from typing import Any, Dict

from openai import AsyncOpenAI
from pipecat_flows import (
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)
from loguru import logger

from backend.models import get_async_patient_db
from backend.sessions import get_async_session_db
from handlers.transcript import save_transcript_to_db

class _MockFlowManager:
    def __init__(self):
        self.state = {}


async def warmup_openai(call_data: dict = None):
    try:
        call_data = call_data or {"organization_name": "Demo Clinic Alpha"}
        flow = MainlineFlow(
            call_data=call_data,
            session_id="warmup",
            flow_manager=_MockFlowManager(),
            main_llm=None,
        )
        greeting_node = flow.create_greeting_node()

        messages = []
        for msg in greeting_node.get("role_messages") or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        for msg in greeting_node.get("task_messages") or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": "Hi"})

        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=1,
        )
        logger.info("OpenAI cache warmed with mainline prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class MainlineFlow:
    WORKFLOW_FLOWS = {
        "scheduling": ("clients.demo_clinic_alpha.patient_scheduling.flow_definition", "PatientSchedulingFlow"),
        "lab_results": ("clients.demo_clinic_alpha.lab_results.flow_definition", "LabResultsFlow"),
        "prescription_status": ("clients.demo_clinic_alpha.prescription_status.flow_definition", "PrescriptionStatusFlow"),
    }

    def __init__(
        self,
        call_data: Dict[str, Any],
        session_id: str,
        flow_manager: FlowManager,
        main_llm,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
        cold_transfer_config: Dict[str, Any] = None,
    ):
        self.call_data = call_data
        self.session_id = session_id
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.organization_name = call_data.get("organization_name", "Demo Clinic Alpha")
        self.cold_transfer_config = cold_transfer_config or {}
        self.practice_info = call_data.get("practice_info", {})
        self._state_initialized = False

        # Initialize state now if flow_manager exists (cross-workflow handoff)
        # For direct dial-in, runner.py calls _init_flow_state() after setting flow_manager
        if self.flow_manager:
            self._init_state()

    def _init_flow_state(self):
        """Called by runner.py after flow_manager is assigned for direct dial-in."""
        if not self._state_initialized:
            self._init_state()

    def _init_state(self):
        if self._state_initialized:
            return
        self._state_initialized = True

        self.flow_manager.state.update({k: "" for k in [
            "caller_name", "call_type", "call_reason", "routed_to", "resolution"
        ]})

    def _get_global_instructions(self) -> str:
        facts_map = [
            ("office_hours", "Office hours", "Monday through Friday, 8 AM to 5 PM"),
            ("location", "Location", None), ("parking", "Parking", None),
            ("new_patient_info", "New patient info", None), ("wait_times", "General wait times", None),
            ("website", "Website", None), ("accepted_insurance", "Accepted insurance", None),
        ]
        practice_facts = []
        for key, label, default in facts_map:
            value = self.practice_info.get(key) or default
            if value:
                practice_facts.append(f"- {label}: {value}")
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
- BILLING: "bill/payment/insurance claim/costs" → route to staff
- CHECK-IN: "check in/arrived/here for appointment" → route to staff immediately
- UNCLEAR or COMPLEX: Anything you're not sure about → route to staff

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
- If the caller is frustrated or asks for a human: route to staff immediately
- Never guess at specific information like appointment availability or account details
- Keep the conversation moving - don't over-explain"""

    def _end_call_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="end_call",
            description="End the call when caller says goodbye or confirms no more questions.",
            properties={},
            required=[],
            handler=self._end_call_handler,
        )

    def get_initial_node(self) -> NodeConfig:
        """Entry point for dial-in calls. Returns the first node to execute."""
        return self.create_greeting_node()

    def create_greeting_node(self) -> NodeConfig:
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
→ NOTE: Use route_to_workflow, NOT request_staff for prescriptions

If caller needs BILLING (payment, bill question, insurance claim, costs):
→ Say "Let me get you to someone who can help with that."
→ Call request_staff with reason="billing question"

If caller needs to CHECK IN for an existing appointment:
→ Say "Let me connect you with the front desk."
→ Call request_staff with reason="check-in for appointment"
→ NOTE: Check-in is NOT scheduling - transfer immediately, don't ask questions

If caller ASKS FOR HUMAN (says "real person", "someone", "transfer me"):
→ Say "Let me transfer you to someone who can help."
→ Call request_staff with reason="caller requested human"

If TRULY UNCLEAR (you genuinely don't understand what they need):
→ Ask one clarifying question first
→ Only transfer if still unclear after that

# Multiple Intents
If caller mentions multiple needs (scheduling AND labs AND billing):
→ Route to the FIRST intent mentioned
→ Include ALL intents in the reason field so the next workflow knows what else is needed
→ Example: reason="annual physical, ALSO: lab results from Tuesday, billing question"
→ The next workflow will handle remaining intents through its own route_to_workflow

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

Caller: "I need to schedule a physical, check my lab results from Tuesday, and ask about a bill."
→ "Got it! Let me help you with all of that, starting with the physical."
→ Call route_to_workflow with workflow="scheduling", reason="annual physical, ALSO NEEDS: lab results from Tuesday, billing question"

# Guardrails
- Gather context naturally, don't interrogate. ONE question max before routing.
- NEVER repeat a question you already asked - even if the caller changes their mind and comes back to the same topic. Track what was discussed:
  - If you already asked about test type → don't ask again, use "blood work" or whatever they said
  - If you already asked about appointment reason → don't ask again, use what they mentioned
  - If caller says "I already told you" or repeats info → acknowledge and move forward
- Be conversational. Don't announce what you're about to do.
- Never guess at specific information like appointment availability or account details.
- If caller is frustrated or asks for a human, call request_staff immediately.
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
                    description="Route caller to AI workflow. Include ALL context in reason.",
                    properties={
                        "workflow": {"type": "string", "enum": ["scheduling", "lab_results", "prescription_status"]},
                        "reason": {"type": "string", "description": "All context: appointment type, doctor, test type, urgency, etc."},
                    },
                    required=["workflow", "reason"],
                    handler=self._route_to_workflow_handler,
                ),
                FlowsFunctionSchema(
                    name="request_staff",
                    description="Transfer to human staff. Use for billing, human requests, or unclear needs.",
                    properties={
                        "reason": {"type": "string", "description": "Brief reason for transfer"},
                    },
                    required=["reason"],
                    handler=self._request_staff_handler,
                ),
                FlowsFunctionSchema(
                    name="save_call_info",
                    description="Save caller's name or reason when volunteered.",
                    properties={
                        "caller_name": {"type": "string"},
                        "call_reason": {"type": "string"},
                    },
                    required=[],
                    handler=self._save_call_info_handler,
                ),
                self._end_call_schema(),
            ],
            respond_immediately=False,
            pre_actions=[
                {"type": "tts_say", "text": greeting_text},
            ],
        )

    def create_transfer_failed_node(self) -> NodeConfig:
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
                    description="Retry the failed transfer when caller requests.",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                self._end_call_schema(),
            ],
            respond_immediately=True,
        )

    async def _save_call_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, None]:
        caller_name = args.get("caller_name", "").strip()
        call_reason = args.get("call_reason", "").strip()

        if caller_name:
            flow_manager.state["caller_name"] = caller_name
        if call_reason:
            flow_manager.state["call_reason"] = call_reason

        logger.info(f"Flow: Saved call info - name={caller_name}, reason={call_reason}")
        return None, None

    async def _route_to_workflow_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")
        flow_manager.state["call_type"] = workflow.replace("_", " ").title()
        flow_manager.state["call_reason"] = reason
        flow_manager.state["routed_to"] = f"{workflow} (AI)"
        flow_manager.state["handed_off_to"] = workflow

        if workflow not in self.WORKFLOW_FLOWS:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you to someone who can.", self.create_transfer_failed_node()

        module_path, class_name = self.WORKFLOW_FLOWS[workflow]
        module = __import__(module_path, fromlist=[class_name])
        FlowClass = getattr(module, class_name)

        flow = FlowClass(
            call_data=self.call_data,
            session_id=self.session_id,
            flow_manager=flow_manager,
            main_llm=self.main_llm,
            context_aggregator=self.context_aggregator,
            transport=self.transport,
            pipeline=self.pipeline,
            organization_id=self.organization_id,
            cold_transfer_config=self.cold_transfer_config,
        )

        logger.info(f"Flow: Handing off to {class_name} with context: {reason}")
        return None, await flow.create_handoff_entry_node(context=reason)

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        reason = args.get("reason", "")
        flow_manager.state["call_type"] = "Staff Transfer"
        flow_manager.state["call_reason"] = reason
        flow_manager.state["routed_to"] = "Staff"
        logger.info(f"Flow: Routing to staff - reason: {reason}")
        return self._initiate_sip_transfer(flow_manager)

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        logger.info("Flow: Call ended - transitioning to end node")
        patient_id = flow_manager.state.get("patient_id")
        session_db = get_async_session_db()

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved to session")

            # Save call metadata to session (works even when patient_id is None)
            session_updates = {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "identity_verified": flow_manager.state.get("identity_verified", False),
                "patient_id": patient_id,
                "caller_name": flow_manager.state.get("caller_name"),
                "call_type": flow_manager.state.get("call_type", "General Question"),
                "call_reason": flow_manager.state.get("call_reason"),
                "routed_to": flow_manager.state.get("routed_to", "Answered Directly"),
            }
            session_updates = {k: v for k, v in session_updates.items() if v is not None}
            await session_db.update_session(self.session_id, session_updates, self.organization_id)
            logger.info(f"Session metadata saved (session_id: {self.session_id})")

            # Update patient record if exists (for cross-workflow handoffs)
            if patient_id:
                patient_db = get_async_patient_db()
                await patient_db.update_patient(patient_id, {
                    "call_status": "Completed",
                    "last_call_session_id": self.session_id,
                }, self.organization_id)

        except Exception as e:
            logger.exception("Error in end_call_handler")
            try:
                await session_db.update_session(self.session_id, {"status": "failed"}, self.organization_id)
            except Exception as db_error:
                logger.error(f"Failed to update session status: {db_error}")

        return None, NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Say a brief, friendly goodbye."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )
