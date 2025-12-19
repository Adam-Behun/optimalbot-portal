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
    """Main phone line - answer patient questions or route to appropriate workflow/department.

    Routes to AI workflows (same call, no transfer):
    - scheduling → PatientSchedulingFlow
    - lab_results → LabResultsFlow (TODO)
    - prescription_status → PrescriptionStatusFlow (TODO)

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
                    "content": """Listen to what the caller needs and respond appropriately:

1. SIMPLE QUESTION you can answer (hours, location, parking, new patient info):
   → Answer it directly in a friendly way, then ask "Is there anything else I can help with?"
   → If they say no/goodbye, call end_call
   → If they have another question, answer it or route as needed

2. NEEDS SCHEDULING (book, cancel, reschedule appointment):
   → Say "Sure, I can help you with that." and call route_to_workflow with workflow="scheduling"

3. NEEDS LAB RESULTS (test results, biopsy, pathology):
   → Say "Let me help you check on that." and call route_to_workflow with workflow="lab_results"

4. NEEDS PRESCRIPTION (refill, medication status):
   → Say "I can look into that for you." and call route_to_workflow with workflow="prescription_status"

5. NEEDS BILLING (payment, bill question, insurance claim, costs):
   → Say "Let me get you to our billing team." and call route_to_staff with department="billing"

6. UNCLEAR, COMPLEX, or ASKS FOR HUMAN:
   → Say "Let me transfer you to someone who can help." and call route_to_staff with department="front_desk"

Be conversational. Don't announce what you're about to do, just do it naturally.""",
                }
            ],
            functions=[
                FlowsFunctionSchema(
                    name="route_to_workflow",
                    description="Route caller to an AI-powered workflow. Use for scheduling, lab results, or prescription status.",
                    properties={
                        "workflow": {
                            "type": "string",
                            "enum": ["scheduling", "lab_results", "prescription_status"],
                            "description": "Workflow to route to: scheduling (appointments), lab_results (test/biopsy results), prescription_status (refills/medications)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for routing, e.g., 'wants to schedule cleaning', 'checking lab results'",
                        },
                    },
                    required=["workflow", "reason"],
                    handler=self._route_to_workflow_handler,
                ),
                FlowsFunctionSchema(
                    name="route_to_staff",
                    description="Transfer caller to human staff via phone. Use for billing or anything that needs a human.",
                    properties={
                        "department": {
                            "type": "string",
                            "enum": ["billing", "front_desk"],
                            "description": "Department to transfer to: billing (payments/insurance claims), front_desk (general/complex/unclear)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for transfer",
                        },
                    },
                    required=["department", "reason"],
                    handler=self._route_to_staff_handler,
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

    def create_transfer_initiated_node(self) -> NodeConfig:
        """Node shown while SIP transfer is in progress."""
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
        """Hand off to PatientSchedulingFlow."""
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

        logger.info("Flow: Handing off to PatientSchedulingFlow")

        # If we already know the reason, skip to scheduling node
        if flow_manager.state.get("call_reason"):
            flow_manager.state["appointment_reason"] = flow_manager.state["call_reason"]
            return None, scheduling_flow.create_greeting_node()

        return None, scheduling_flow.create_greeting_node()

    async def _handoff_to_lab_results(
        self, flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Hand off to LabResultsFlow.

        TODO: Implement LabResultsFlow at clients/demo_clinic_alpha/lab_results/flow_definition.py
        For now, falls back to SIP transfer to medical staff.
        """
        logger.warning("Flow: LabResultsFlow not yet implemented, falling back to SIP transfer")

        # Fallback: SIP transfer to medical staff until workflow is implemented
        flow_manager.state["routed_to"] = "Medical (staff)"
        return await self._initiate_sip_transfer(flow_manager, "medical")

    async def _handoff_to_prescription_status(
        self, flow_manager: FlowManager
    ) -> tuple[str, NodeConfig]:
        """Hand off to PrescriptionStatusFlow.

        TODO: Implement PrescriptionStatusFlow at clients/demo_clinic_alpha/prescription_status/flow_definition.py
        For now, falls back to SIP transfer to medical staff.
        """
        logger.warning("Flow: PrescriptionStatusFlow not yet implemented, falling back to SIP transfer")

        # Fallback: SIP transfer to medical staff until workflow is implemented
        flow_manager.state["routed_to"] = "Medical (staff)"
        return await self._initiate_sip_transfer(flow_manager, "medical")

    async def _route_to_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, NodeConfig]:
        """Route to human staff via SIP transfer."""
        department = args.get("department", "front_desk")
        reason = args.get("reason", "")

        flow_manager.state["call_type"] = department.replace("_", " ").title()
        flow_manager.state["call_reason"] = reason
        flow_manager.state["routed_to"] = f"{department.replace('_', ' ').title()} (staff)"

        logger.info(f"Flow: Routing to {department} staff - reason: {reason}")

        return await self._initiate_sip_transfer(flow_manager, department)

    async def _initiate_sip_transfer(
        self, flow_manager: FlowManager, department: str
    ) -> tuple[None, NodeConfig]:
        """Initiate SIP transfer to a phone number."""
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
