import asyncio

from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame

from backend.sessions import get_async_session_db
from backend.utils import normalize_sip_endpoint


def _estimate_tts_duration(text: str) -> float:
    words = len(text.split())
    return (words / 3) + 0.5


async def _safety_sip_transfer(pipeline, status: str):
    cold_transfer = getattr(pipeline.flow, 'cold_transfer_config', None) or {}
    staff_number = normalize_sip_endpoint(cold_transfer.get('staff_number'))
    if not staff_number:
        logger.warning("No staff_number configured for transfer")
        return False
    try:
        pipeline.transfer_in_progress = True
        error = await pipeline.transport.sip_call_transfer({"toEndPoint": staff_number})
        if error:
            logger.error(f"SIP transfer failed: {error}")
            pipeline.transfer_in_progress = False
            return False
        logger.info(f"SIP transfer initiated to {staff_number}")
        session_db = get_async_session_db()
        await session_db.update_session(pipeline.session_id, {"call_status": status}, pipeline.organization_id)
        return True
    except Exception as e:
        logger.error(f"Transfer failed: {e}")
        pipeline.transfer_in_progress = False
        return False


def setup_safety_handlers(pipeline, safety_monitor, config):
    @safety_monitor.event_handler("on_emergency_detected")
    async def handle_emergency(processor):
        logger.warning(f"EMERGENCY detected - session {pipeline.session_id}")
        msg = config.get("emergency_message", "If this is an emergency, hang up and dial 911.")
        await pipeline.task.queue_frames([TTSSpeakFrame(msg)])
        if config.get("auto_transfer"):
            await asyncio.sleep(_estimate_tts_duration(msg))
            await _safety_sip_transfer(pipeline, "Emergency")

    @safety_monitor.event_handler("on_staff_requested")
    async def handle_staff_request(processor):
        logger.info(f"Staff transfer requested - session {pipeline.session_id}")
        msg = "Transferring you now, please hold."
        await pipeline.task.queue_frames([TTSSpeakFrame(msg)])
        await asyncio.sleep(_estimate_tts_duration(msg))
        await _safety_sip_transfer(pipeline, "Transferred")


def setup_output_validator_handlers(pipeline, output_validator, config):
    @output_validator.event_handler("on_unsafe_output")
    async def handle_unsafe_output(processor, text):
        logger.warning(f"UNSAFE output detected - session {pipeline.session_id}: {text[:100]}...")
        msg = config.get("unsafe_output_message", "I apologize, let me rephrase that.")
        await pipeline.task.queue_frames([TTSSpeakFrame(msg)])
