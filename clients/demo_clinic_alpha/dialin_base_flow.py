from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Any

from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger

from backend.models import get_async_patient_db
from backend.sessions import get_async_session_db
from backend.utils import parse_natural_date
from handlers.transcript import save_transcript_to_db


class DialinBaseFlow(ABC):
    ALLOWS_NEW_PATIENTS = False
    WORKFLOW_FLOWS: Dict[str, tuple] = {}
    CONFIRM_BEFORE_TRANSFER = False

    def __init__(
        self,
        call_data: Dict[str, Any],
        session_id: str,
        flow_manager: FlowManager,
        main_llm=None,
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
        self._state_initialized = False
        if self.flow_manager:
            self._init_state()

    def _init_flow_state(self):
        if not self._state_initialized:
            self._init_state()

    def _init_state(self):
        if self._state_initialized:
            return
        self._state_initialized = True
        state = self.flow_manager.state
        for field in ["patient_id", "patient_name", "first_name", "last_name", "date_of_birth", "phone_number"]:
            default = None if field == "patient_id" else ""
            state[field] = state.get(field) or self.call_data.get(field, default)
        state.setdefault("identity_verified", False)
        state.setdefault("routed_to", "")
        state.setdefault("lookup_attempts", 0)
        self._init_domain_state()

    def _init_domain_state(self):
        pass

    # ==================== Abstract Methods ====================

    @abstractmethod
    def _get_workflow_type(self) -> str:
        pass

    @abstractmethod
    def _get_global_instructions(self) -> str:
        pass

    @abstractmethod
    def _extract_lookup_record(self, patient: dict) -> dict:
        pass

    @abstractmethod
    def _populate_domain_state(self, flow_manager: FlowManager, lookup: dict):
        pass

    @abstractmethod
    def _route_after_verification(self, flow_manager: FlowManager) -> NodeConfig:
        pass

    @abstractmethod
    async def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        """Entry point when routed from another workflow."""
        pass

    # ==================== Hook Methods ====================

    def _get_verification_greeting(self, first_name: str) -> str | None:
        """Override to return a greeting after successful verification."""
        return None

    # ==================== Helpers ====================

    def _normalize_phone(self, phone: str) -> str:
        return ''.join(c for c in phone if c.isdigit())

    def _phone_last4(self, phone: str) -> str:
        return phone[-4:] if len(phone) >= 4 else ""

    async def _try_db_update(self, patient_id: str, method: str, *args, error_msg: str = "DB update error"):
        if not patient_id:
            return
        try:
            db = get_async_patient_db()
            await getattr(db, method)(patient_id, *args, self.organization_id)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")

    # ==================== Shared Schemas ====================

    def _end_call_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="end_call",
            description="End call. Use when caller says goodbye/bye/that's all. NOT just 'thank you'.",
            properties={},
            required=[],
            handler=self._end_call_handler,
        )

    def _request_staff_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="request_staff",
            description="Transfer to staff. Use when caller explicitly asks for human/transfer, or for billing.",
            properties={"reason": {"type": "string", "description": "Brief reason for transfer"}},
            required=[],
            handler=self._request_staff_handler,
        )

    def _route_to_workflow_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="route_to_workflow",
            description="Route to another workflow.",
            properties={
                "workflow": {"type": "string", "enum": list(self.WORKFLOW_FLOWS.keys())},
                "reason": {"type": "string", "description": "Brief context for handoff"},
            },
            required=["workflow", "reason"],
            handler=self._route_to_workflow_handler,
        )

    async def _route_to_workflow_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        workflow = args.get("workflow", "")
        reason = args.get("reason", "")
        flow_manager.state["routed_to"] = f"{workflow} (AI)"
        logger.info(f"Flow: Routing to {workflow} workflow - reason: {reason}")

        if workflow not in self.WORKFLOW_FLOWS:
            logger.warning(f"Unknown workflow: {workflow}")
            return "I'm not sure how to help with that. Let me transfer you.", self.create_transfer_failed_node()

        module_path, class_name = self.WORKFLOW_FLOWS[workflow]
        module = __import__(module_path, fromlist=[class_name])
        FlowClass = getattr(module, class_name)
        target_flow = FlowClass(
            call_data=self.call_data, session_id=self.session_id, flow_manager=flow_manager,
            main_llm=self.main_llm, context_aggregator=self.context_aggregator,
            transport=self.transport, pipeline=self.pipeline,
            organization_id=self.organization_id, cold_transfer_config=self.cold_transfer_config,
        )

        first_name = flow_manager.state.get("first_name", "")
        msg = f"Let me help with that, {first_name}!" if first_name else "Let me help with that!"

        return msg, await target_flow.create_handoff_entry_node(context=reason)

    # ==================== Verification Nodes ====================

    def create_patient_lookup_node(self) -> NodeConfig:
        return NodeConfig(
            name="patient_lookup",
            task_messages=[{
                "role": "system",
                "content": """You just asked for the phone number. Wait for the caller to provide it.

# Phone Normalization
Spoken → Written (digits only):
- "five five five one two three four" → "5551234"
- "555-123-4567" → "5551234567"

# IMPORTANT: Confirm Before Lookup
After collecting the number, READ IT BACK to confirm before calling lookup_by_phone.
Format: "That's [number formatted as XXX-XXX-XXXX], correct?"

Example:
Caller: "five one six, five six six, seven one three two"
You: "That's 516-566-7132, correct?"
Caller: "Yes"
→ Call lookup_by_phone(phone_number="5165667132")

If caller says the number is wrong, ask them to repeat it.
If unclear, ask: "Could you repeat that number?"
If caller doesn't know their number, call request_staff.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="lookup_by_phone",
                    description="Look up patient by phone. Call after collecting phone number.",
                    properties={"phone_number": {"type": "string", "description": "Digits only (e.g., '5551234567')"}},
                    required=["phone_number"],
                    handler=self._lookup_by_phone_handler,
                ),
                self._request_staff_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": "Sounds good! What's the phone number on your account?"}],
            respond_immediately=False,
        )

    def create_verify_dob_node(self) -> NodeConfig:
        return NodeConfig(
            name="verify_dob",
            task_messages=[{
                "role": "system",
                "content": """Wait for the caller to provide their date of birth.

# Date Normalization
Spoken → Written:
- "march twenty second seventy eight" → "March 22, 1978"
- "three twenty two nineteen seventy eight" → "March 22, 1978"

Once you have DOB, call verify_dob immediately.

If unclear, ask: "Could you repeat that date?"
If caller can't verify, call request_staff.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="verify_dob",
                    description="Verify patient by DOB. Call after collecting date of birth.",
                    properties={"date_of_birth": {"type": "string", "description": "Natural format (e.g., 'March 22, 1978')"}},
                    required=["date_of_birth"],
                    handler=self._verify_dob_handler,
                ),
                self._request_staff_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": "Can you confirm your date of birth please?"}],
            post_actions=[{"type": "tts_say", "text": "Let me pull up your account."}],
            respond_immediately=False,
        )

    def create_patient_not_found_node(self) -> NodeConfig:
        state = self.flow_manager.state
        phone = state.get("_last_lookup_phone", "")
        phone_display = f"{phone[:3]}-{phone[3:6]}-{phone[6:]}" if len(phone) == 10 else phone
        return NodeConfig(
            name="patient_not_found",
            task_messages=[{
                "role": "system",
                "content": """The patient wasn't found. Allow them to retry with different info.

# Rules
- If caller provides new phone AND/OR date of birth → call retry_lookup with the new values
- If caller wants to speak to someone → call request_staff
- If caller says goodbye → call end_call

# Phone/DOB Normalization
Phone: digits only (e.g., "5551234567")
DOB: natural format (e.g., "March 22, 1978")""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="retry_lookup",
                    description="Retry lookup with corrected phone and/or DOB.",
                    properties={
                        "phone_number": {"type": "string", "description": "Corrected phone (digits only), or same if unchanged"},
                        "date_of_birth": {"type": "string", "description": "Corrected DOB (natural format), or same if unchanged"},
                    },
                    required=["phone_number", "date_of_birth"],
                    handler=self._retry_lookup_handler,
                ),
                self._request_staff_schema(),
                self._end_call_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": f"I'm sorry, I couldn't find a record for {phone_display} with that date of birth. Could you double-check those for me?"}],
            respond_immediately=False,
        )

    def create_patient_not_found_final_node(self) -> NodeConfig:
        return NodeConfig(
            name="patient_not_found_final",
            task_messages=[],
            functions=[],
            pre_actions=[{"type": "tts_say", "text": "I still couldn't find your record. Let me connect you with a colleague who can help."}],
            post_actions=[{"type": "end_conversation"}],
        )

    # ==================== Transfer Nodes ====================

    def create_transfer_initiated_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_initiated",
            task_messages=[],
            functions=[],
            pre_actions=[{"type": "tts_say", "text": "Transferring you now, please hold."}],
            post_actions=[{"type": "end_conversation"}],
        )

    def create_transfer_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_failed",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """The transfer didn't go through. Wait for caller's response.

If caller wants to try again:
→ Call retry_transfer

If caller says goodbye:
→ Call end_call""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="retry_transfer",
                    description="Retry the failed transfer.",
                    properties={},
                    required=[],
                    handler=self._retry_transfer_handler,
                ),
                self._end_call_schema(),
            ],
            pre_actions=[{"type": "tts_say", "text": "I apologize, the transfer didn't go through."}],
            respond_immediately=False,
        )

    # ==================== Verification Handlers ====================

    async def _lookup_by_phone_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        phone_digits = self._normalize_phone(args.get("phone_number", ""))
        logger.info(f"Flow: Looking up phone: {self._phone_last4(phone_digits)}")
        flow_manager.state["_last_lookup_phone"] = phone_digits
        patient = await get_async_patient_db().find_patient_by_phone(phone_digits, self.organization_id, self._get_workflow_type())
        if patient:
            stored_dob = patient.get("date_of_birth", "")
            if not stored_dob:
                logger.warning("Flow: Patient found but no DOB on file - transferring to staff")
                await self._initiate_sip_transfer(flow_manager)
                return None, self.create_patient_not_found_final_node()
            flow_manager.state["_lookup_record"] = self._extract_lookup_record(patient)
            logger.info("Flow: Found record, requesting DOB")
            return None, self.create_verify_dob_node()
        flow_manager.state["lookup_attempts"] = flow_manager.state.get("lookup_attempts", 0) + 1
        if flow_manager.state["lookup_attempts"] >= 2:
            logger.info("Flow: No patient found after 2 attempts - transferring to staff")
            await self._initiate_sip_transfer(flow_manager)
            return None, self.create_patient_not_found_final_node()
        logger.info("Flow: No patient found - offering retry")
        flow_manager.state["_last_lookup_dob"] = ""
        return None, self.create_patient_not_found_node()

    async def _verify_dob_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        provided = parse_natural_date(args.get("date_of_birth", "").strip())
        lookup = flow_manager.state.get("_lookup_record", {})
        stored = lookup.get("date_of_birth", "")
        logger.info(f"Flow: Verifying DOB - provided: {provided}, stored: {stored}")
        flow_manager.state["_last_lookup_dob"] = provided or args.get("date_of_birth", "").strip()
        if not provided or provided != stored:
            logger.warning("Flow: DOB mismatch")
            flow_manager.state.pop("_lookup_record", None)
            flow_manager.state["lookup_attempts"] = flow_manager.state.get("lookup_attempts", 0) + 1
            if flow_manager.state["lookup_attempts"] >= 2:
                await self._initiate_sip_transfer(flow_manager)
                return None, self.create_patient_not_found_final_node()
            return None, self.create_patient_not_found_node()
        flow_manager.state["identity_verified"] = True
        flow_manager.state["patient_id"] = lookup.get("patient_id")
        flow_manager.state["first_name"] = lookup.get("first_name", "")
        flow_manager.state["last_name"] = lookup.get("last_name", "")
        flow_manager.state["date_of_birth"] = stored
        flow_manager.state["phone_number"] = lookup.get("phone_number", "")
        flow_manager.state["patient_name"] = f"{lookup.get('first_name', '')} {lookup.get('last_name', '')}".strip()
        self._populate_domain_state(flow_manager, lookup)
        flow_manager.state.pop("_lookup_record", None)
        greeting = self._get_verification_greeting(lookup.get("first_name", ""))
        logger.info(f"Flow: DOB verified for {lookup.get('first_name', '')}")
        return greeting, self._route_after_verification(flow_manager)

    async def _retry_lookup_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        phone_digits = self._normalize_phone(args.get("phone_number", ""))
        provided_dob = args.get("date_of_birth", "").strip()
        normalized_dob = parse_natural_date(provided_dob) if provided_dob else None
        logger.info(f"Flow: Retry lookup - phone: {self._phone_last4(phone_digits)}, dob: {normalized_dob}")
        flow_manager.state["_last_lookup_phone"] = phone_digits
        flow_manager.state["_last_lookup_dob"] = normalized_dob or provided_dob
        patient = await get_async_patient_db().find_patient_by_phone(phone_digits, self.organization_id, self._get_workflow_type())
        if patient:
            stored_dob = patient.get("date_of_birth", "")
            if normalized_dob and normalized_dob == stored_dob:
                flow_manager.state["identity_verified"] = True
                flow_manager.state["patient_id"] = patient.get("patient_id")
                flow_manager.state["first_name"] = patient.get("first_name", "")
                flow_manager.state["last_name"] = patient.get("last_name", "")
                flow_manager.state["date_of_birth"] = stored_dob
                flow_manager.state["phone_number"] = patient.get("phone_number", "")
                flow_manager.state["patient_name"] = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
                lookup = self._extract_lookup_record(patient)
                self._populate_domain_state(flow_manager, lookup)
                greeting = self._get_verification_greeting(patient.get("first_name", ""))
                logger.info("Flow: Retry successful - patient verified")
                return greeting, self._route_after_verification(flow_manager)
        logger.info("Flow: Retry failed - transferring to staff")
        await self._initiate_sip_transfer(flow_manager)
        return None, self.create_patient_not_found_final_node()

    # ==================== Transfer Handlers ====================

    async def _initiate_sip_transfer(self, flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        staff_number = self.cold_transfer_config.get("staff_number")
        if not staff_number:
            logger.warning("No staff transfer number configured")
            return None, self.create_transfer_failed_node()
        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True
            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"SIP transfer initiated: {staff_number}")
            patient_id = flow_manager.state.get("patient_id")
            await self._try_db_update(patient_id, "update_patient", {"call_status": "Transferred"}, error_msg="Error updating call status")
            return None, self.create_transfer_initiated_node()
        except Exception:
            logger.exception("SIP transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False
            return None, self.create_transfer_failed_node()

    async def _request_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        reason = args.get("reason", "caller requested transfer")
        logger.info(f"Flow: Staff transfer requested - reason: {reason}")
        if self.CONFIRM_BEFORE_TRANSFER and hasattr(self, 'create_staff_confirmation_node'):
            return None, self.create_staff_confirmation_node()
        return await self._initiate_sip_transfer(flow_manager)

    async def _retry_transfer_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Retrying SIP transfer")
        return await self._initiate_sip_transfer(flow_manager)

    async def _initiate_transfer_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Initiating transfer after message")
        return await self._initiate_sip_transfer(flow_manager)

    # ==================== End Call Handler ====================

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Ending call")
        patient_id = flow_manager.state.get("patient_id")
        session_db = get_async_session_db()
        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
            session_updates = {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "identity_verified": flow_manager.state.get("identity_verified", False),
                "patient_id": patient_id,
            }
            await session_db.update_session(self.session_id, session_updates, self.organization_id)
            if patient_id:
                patient_db = get_async_patient_db()
                await patient_db.update_patient(patient_id, {
                    "call_status": "Completed",
                    "last_call_session_id": self.session_id,
                }, self.organization_id)
        except Exception:
            logger.exception("Error in end_call_handler")
            try:
                await session_db.update_session(self.session_id, {"status": "failed"}, self.organization_id)
            except Exception as db_error:
                logger.error(f"Failed to update session status: {db_error}")
        return None, NodeConfig(
            name="end",
            task_messages=[{"role": "system", "content": "Thank the patient and say goodbye warmly."}],
            functions=[],
            post_actions=[{"type": "end_conversation"}],
        )
