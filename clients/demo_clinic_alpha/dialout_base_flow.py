from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Any

from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from pipecat_flows.types import ActionConfig
from loguru import logger

from backend.models.patient import get_async_patient_db
from backend.sessions import get_async_session_db
from backend.utils import normalize_sip_endpoint


class DialoutBaseFlow(ABC):
    """Base class for outbound call flows (dial-out).

    Provides shared functionality for dial-out workflows:
    - DB update helpers (_try_db_update, _record_field)
    - Transfer nodes and handlers
    - End call handling
    - State initialization patterns
    """

    def __init__(
        self,
        patient_data: Dict[str, Any],
        session_id: str,
        flow_manager: FlowManager = None,
        main_llm=None,
        classifier_llm=None,
        context_aggregator=None,
        transport=None,
        pipeline=None,
        organization_id: str = None,
        cold_transfer_config: Dict[str, Any] = None,
    ):
        self.flow_manager = flow_manager
        self.session_id = session_id
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.cold_transfer_config = cold_transfer_config or {}
        self._patient_data = patient_data
        self._state_initialized = False

    # ==================== Abstract Methods ====================

    @abstractmethod
    def _get_global_instructions(self) -> str:
        """Return bot persona and behavior instructions."""
        pass

    @abstractmethod
    def get_triage_config(self) -> dict:
        """Return triage configuration for IVR/voicemail detection."""
        pass

    # ==================== State Initialization ====================

    def _init_flow_state(self):
        """Initialize flow_manager state. Called after flow_manager is set."""
        if not self.flow_manager or self._state_initialized:
            return
        self._state_initialized = True
        self._init_common_state()
        self._init_domain_state()

    def _init_common_state(self):
        """Initialize common fields from patient_data."""
        self.flow_manager.state["patient_id"] = self._patient_data.get("patient_id")
        self.flow_manager.state["patient_name"] = self._patient_data.get("patient_name", "")
        self.flow_manager.state["date_of_birth"] = self._patient_data.get("date_of_birth", "")

    def _init_domain_state(self):
        """Override to initialize workflow-specific state."""
        pass

    # ==================== DB Helpers ====================

    async def _try_db_update(self, patient_id: str, method: str, *args, error_msg: str = "DB update error"):
        """Generic DB update with error handling."""
        if not patient_id:
            logger.warning(f"DB update skipped - no patient_id for {method}")
            return
        try:
            db = get_async_patient_db()
            logger.info(f"DB update: {method}({patient_id}, {args})")
            await getattr(db, method)(patient_id, *args, self.organization_id)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")

    async def _record_field(self, field_name: str, value: Any, flow_manager: FlowManager) -> tuple[None, None]:
        """Record a single field to state and DB. Returns (None, None) to stay on current node."""
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state[field_name] = value
        await self._try_db_update(patient_id, "update_field", field_name, value)
        logger.debug(f"[Flow] Recorded: {field_name}={value}")
        return None, None

    # ==================== Shared Schemas ====================

    def _end_call_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="end_call",
            description="End call. Use after saying goodbye.",
            properties={},
            required=[],
            handler=self._end_call_handler,
        )

    def _request_staff_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="request_staff",
            description="Transfer to manager when rep asks.",
            properties={},
            required=[],
            handler=self._request_staff_handler,
        )

    # ==================== Transfer Nodes ====================

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
- If no/nevermind/continue → call decline_transfer"""
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
                    name="decline_transfer",
                    description="Continue to call wrap-up if they decline transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_closing_handler
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
            post_actions=[{
                "type": "end_conversation"
            }]
        )

    def create_transfer_pending_node(self) -> NodeConfig:
        """Node that plays TTS, then executes SIP transfer after speech completes."""
        return NodeConfig(
            name="transfer_pending",
            task_messages=[],
            functions=[],
            pre_actions=[
                {"type": "tts_say", "text": "Transferring you now, please hold."},
                ActionConfig(type="function", handler=self._regular_sip_transfer),
            ],
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
                "content": "The transfer failed. Apologize and wrap up the call."
            }],
            functions=[
                FlowsFunctionSchema(
                    name="wrap_up_call",
                    description="Proceed to call wrap-up after failed transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_closing_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "tts_say",
                "text": "I apologize, the transfer didn't go through."
            }]
        )

    # ==================== Transfer Handlers ====================

    async def _regular_sip_transfer(self, action: dict, flow_manager: FlowManager):
        staff_number = normalize_sip_endpoint(self.cold_transfer_config.get("staff_number"))
        if not staff_number:
            logger.warning("No staff transfer number configured")
            return
        try:
            if self.pipeline:
                self.pipeline.transfer_in_progress = True
            if self.transport:
                error = await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                if error:
                    logger.error(f"SIP transfer failed: {error}")
                    if self.pipeline:
                        self.pipeline.transfer_in_progress = False
                    return
                logger.info(f"SIP transfer initiated: {staff_number}")
            session_db = get_async_session_db()
            await session_db.update_session(self.session_id, {"call_status": "Transferred"}, self.organization_id)
        except Exception:
            logger.exception("SIP transfer failed")
            if self.pipeline:
                self.pipeline.transfer_in_progress = False

    async def _request_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("[Flow] Staff transfer requested")
        return None, self.create_staff_confirmation_node()

    async def _dial_staff_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        staff_number = normalize_sip_endpoint(self.cold_transfer_config.get("staff_number"))
        if not staff_number:
            logger.warning("Cold transfer requested but no staff_number configured")
            return None, self.create_transfer_failed_node()
        logger.info(f"[Flow] Cold transfer initiated to: {staff_number}")
        return None, self.create_transfer_pending_node()

    async def _return_to_closing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        """Return to closing node after transfer declined/failed. Override if needed."""
        logger.info("[Flow] Returning to closing (transfer declined/failed)")
        return None, self.create_closing_node()

    @abstractmethod
    def create_closing_node(self) -> NodeConfig:
        """Create the closing node. Must be implemented by subclass."""
        pass

    # ==================== End Call Handler ====================

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, None]:
        from pipecat.frames.frames import EndTaskFrame
        from pipecat.processors.frame_processor import FrameDirection

        logger.info("[Flow] Call ended")
        patient_id = flow_manager.state.get("patient_id")
        session_db = get_async_session_db()

        try:
            # NOTE: Transcript is saved by transport event handlers (cleanup_and_cancel)
            # after the call actually ends, ensuring all messages are captured.

            session_updates = {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "patient_id": patient_id,
            }
            await session_db.update_session(self.session_id, session_updates, self.organization_id)

            if patient_id:
                patient_db = get_async_patient_db()
                await patient_db.update_patient(patient_id, {
                    "call_status": "Completed",
                    "last_call_session_id": self.session_id,
                }, self.organization_id)

            if self.context_aggregator:
                await self.context_aggregator.assistant().push_frame(
                    EndTaskFrame(), FrameDirection.UPSTREAM
                )

        except Exception as e:
            logger.error(f"Error in end_call_handler: {e}")
            try:
                await session_db.update_session(self.session_id, {"status": "failed"}, self.organization_id)
            except Exception:
                pass

        return None, None
