import asyncio
import random
from datetime import datetime

from loguru import logger
from pipecat.frames.frames import EndFrame

from backend.constants import CallStatus
from backend.models.patient import get_async_patient_db
from backend.sessions import get_async_session_db
from handlers.transcript import save_transcript_to_db

DIALOUT_MAX_RETRIES = 3
DIALOUT_BASE_DELAY = 1.0  # Base delay in seconds for exponential backoff
DIALOUT_MAX_JITTER = 0.5  # Maximum jitter in seconds
TERMINAL_STATUSES = [CallStatus.COMPLETED.value, CallStatus.SUPERVISOR_DIALED.value, CallStatus.FAILED.value]


class DialoutManager:
    def __init__(self, transport, phone_number: str):
        self.transport = transport
        self.phone_number = phone_number
        self.attempt_count = 0
        self.is_connected = False

    def _calculate_delay(self) -> float:
        """Calculate delay with exponential backoff + jitter.

        Attempt 1->2: 1s base, Attempt 2->3: 2s base, Attempt 3->4: 4s base
        Plus random jitter 0-0.5s to prevent thundering herd.
        """
        base_delay = DIALOUT_BASE_DELAY * (2 ** (self.attempt_count - 1))
        jitter = random.uniform(0, DIALOUT_MAX_JITTER)
        return base_delay + jitter

    async def attempt(self) -> bool:
        if self.attempt_count >= DIALOUT_MAX_RETRIES or self.is_connected:
            return False
        self.attempt_count += 1
        logger.info(f"[Call] Dialout attempt {self.attempt_count}/{DIALOUT_MAX_RETRIES} to {self.phone_number}")
        await self.transport.start_dialout({"phoneNumber": self.phone_number})
        return True

    async def retry(self) -> bool:
        if not self.should_retry():
            return False
        delay = self._calculate_delay()
        logger.info(f"[Call] Retrying dialout in {delay:.2f}s (attempt {self.attempt_count + 1}/{DIALOUT_MAX_RETRIES})")
        await asyncio.sleep(delay)
        return await self.attempt()

    def mark_connected(self):
        self.is_connected = True

    def should_retry(self) -> bool:
        return self.attempt_count < DIALOUT_MAX_RETRIES and not self.is_connected


async def update_status_if_not_terminal(pipeline, new_status: CallStatus):
    if not pipeline.patient_id:
        logger.debug("No patient_id - skipping status update")
        return
    try:
        patient = await get_async_patient_db().find_patient_by_id(
            pipeline.patient_id, pipeline.organization_id
        )
        current_status = patient.get("call_status") if patient else None
        if current_status not in TERMINAL_STATUSES:
            await get_async_patient_db().update_call_status(
                pipeline.patient_id, new_status.value, pipeline.organization_id
            )
            logger.info(f"Status updated: {new_status.value}")
        else:
            logger.info(f"Status already terminal: {current_status}")
    except Exception as e:
        logger.error(f"Error updating status: {e}")


async def save_usage_costs(pipeline):
    """Save usage and cost data to session."""
    if not hasattr(pipeline, 'usage_observer') or not pipeline.usage_observer:
        logger.warning(f"No usage observer for session {pipeline.session_id}, costs not tracked")
        return
    try:
        costs = pipeline.usage_observer.get_usage_summary()
        success = await get_async_session_db().update_session(
            pipeline.session_id,
            {
                "usage": costs.get("usage"),
                "costs": costs.get("costs"),
                "total_cost_usd": costs.get("total_cost_usd")
            },
            pipeline.organization_id
        )
        if success:
            logger.info(f"Usage costs saved: ${costs.get('total_cost_usd', 0):.4f}")
        else:
            logger.error(f"Failed to save usage costs for session {pipeline.session_id}")
    except Exception:
        logger.exception(f"Error saving usage costs for session {pipeline.session_id}")


async def cleanup_and_cancel(pipeline):
    await save_transcript_to_db(pipeline)
    await save_usage_costs(pipeline)
    if pipeline.task:
        await pipeline.task.cancel()
        logger.info("Pipeline cancelled")


def setup_transport_handlers(pipeline, call_type: str):
    if call_type == "dial-in":
        setup_dialin_handlers(pipeline)
    else:
        setup_dialout_handlers(pipeline)


def setup_dialin_handlers(pipeline):

    @pipeline.transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info(f"[Call] Connected: {participant['id']}")
        if hasattr(pipeline, 'usage_observer') and pipeline.usage_observer:
            pipeline.usage_observer.mark_call_connected()
        await transport.capture_participant_transcription(participant["id"])
        if pipeline.flow and pipeline.flow_manager:
            initial_node = pipeline.flow.get_initial_node()
            await pipeline.flow_manager.initialize(initial_node)
            logger.info("Flow initialized with initial node")

    @pipeline.transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("[Call] Disconnected")
        if hasattr(pipeline, 'usage_observer') and pipeline.usage_observer:
            pipeline.usage_observer.mark_call_ended()
        await update_status_if_not_terminal(pipeline, CallStatus.COMPLETED)
        await cleanup_and_cancel(pipeline)

    @pipeline.transport.event_handler("on_dialin_error")
    async def on_dialin_error(transport, data):
        logger.error(f"[Call] Dial-in error: {data}")
        if pipeline.patient_id:
            await get_async_patient_db().update_call_status(
                pipeline.patient_id, CallStatus.FAILED.value, pipeline.organization_id
            )
        await cleanup_and_cancel(pipeline)

    @pipeline.transport.event_handler("on_dialout_answered")
    async def on_dialout_answered(transport, data):
        if not pipeline.transfer_in_progress:
            return
        logger.info("[Call] Transfer completed - staff answered")
        pipeline.transcripts.append({
            "role": "system",
            "content": "Call transferred to staff",
            "timestamp": datetime.utcnow().isoformat(),
            "type": "transfer"
        })
        if pipeline.patient_id:
            await get_async_patient_db().update_call_status(
                pipeline.patient_id, CallStatus.COMPLETED.value, pipeline.organization_id
            )
        await save_transcript_to_db(pipeline)
        await pipeline.task.queue_frames([EndFrame()])

    @pipeline.transport.event_handler("on_dialout_error")
    async def on_dialout_error(transport, data):
        if not pipeline.transfer_in_progress:
            return
        logger.error(f"[Call] Transfer failed: {data}")
        pipeline.transfer_in_progress = False
        pipeline.transcripts.append({
            "role": "system",
            "content": "Transfer to staff failed",
            "timestamp": datetime.utcnow().isoformat(),
            "type": "transfer"
        })


def setup_dialout_handlers(pipeline):
    dialout_manager = DialoutManager(pipeline.transport, pipeline.phone_number)
    pipeline.dialout_manager = dialout_manager

    @pipeline.transport.event_handler("on_joined")
    async def on_joined(transport, data):
        logger.info(f"[Call] Bot joined room, dialing {pipeline.phone_number}")
        await dialout_manager.attempt()

    @pipeline.transport.event_handler("on_dialout_answered")
    async def on_dialout_answered(transport, data):
        if pipeline.transfer_in_progress:
            logger.info("[Call] Supervisor transfer completed")
            pipeline.transcripts.append({
                "role": "system",
                "content": "Call transferred to supervisor",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })
            if pipeline.patient_id:
                await get_async_patient_db().update_call_status(
                    pipeline.patient_id, CallStatus.SUPERVISOR_DIALED.value, pipeline.organization_id
                )
            await save_transcript_to_db(pipeline)
            await pipeline.task.queue_frames([EndFrame()])
        else:
            dialout_manager.mark_connected()
            if hasattr(pipeline, 'usage_observer') and pipeline.usage_observer:
                pipeline.usage_observer.mark_call_connected()
            if pipeline.patient_id:
                await get_async_patient_db().update_call_status(
                    pipeline.patient_id, CallStatus.IN_PROGRESS.value, pipeline.organization_id
                )
            logger.info(f"[Call] Answered by {pipeline.phone_number}")

    @pipeline.transport.event_handler("on_dialout_stopped")
    async def on_dialout_stopped(transport, data):
        logger.info("[Call] Dialout stopped")
        if hasattr(pipeline, 'usage_observer') and pipeline.usage_observer:
            pipeline.usage_observer.mark_call_ended()
        await update_status_if_not_terminal(pipeline, CallStatus.COMPLETED)
        await cleanup_and_cancel(pipeline)

    @pipeline.transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, data):
        logger.info("[Call] Participant left")
        if hasattr(pipeline, 'usage_observer') and pipeline.usage_observer:
            pipeline.usage_observer.mark_call_ended()
        await update_status_if_not_terminal(pipeline, CallStatus.COMPLETED)
        await cleanup_and_cancel(pipeline)

    @pipeline.transport.event_handler("on_dialout_error")
    async def on_dialout_error(transport, data):
        if pipeline.transfer_in_progress:
            logger.error("[Call] Supervisor transfer failed - continuing call")
            pipeline.transfer_in_progress = False
            pipeline.transcripts.append({
                "role": "system",
                "content": "Transfer to supervisor failed",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })
            return

        logger.warning(f"[Call] Dialout error (attempt {dialout_manager.attempt_count}): {data}")
        if await dialout_manager.retry():
            return

        logger.error(f"[Call] All {DIALOUT_MAX_RETRIES} dialout attempts failed")
        if pipeline.patient_id:
            await get_async_patient_db().update_call_status(
                pipeline.patient_id, CallStatus.FAILED.value, pipeline.organization_id
            )
        await cleanup_and_cancel(pipeline)
