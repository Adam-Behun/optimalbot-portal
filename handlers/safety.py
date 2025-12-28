from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame


async def _initiate_transfer(pipeline):
    cold_transfer = getattr(pipeline.flow, 'cold_transfer_config', None) or {}
    staff_number = cold_transfer.get('staff_number')
    if not staff_number:
        logger.warning("No staff_number configured for transfer")
        return False

    try:
        pipeline.transfer_in_progress = True
        await pipeline.transport.sip_call_transfer({"toEndPoint": staff_number})
        logger.info(f"SIP transfer initiated to {staff_number}")
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
            await _initiate_transfer(pipeline)

    @safety_monitor.event_handler("on_staff_requested")
    async def handle_staff_request(processor):
        logger.info(f"Staff transfer requested - session {pipeline.session_id}")
        await pipeline.task.queue_frames([TTSSpeakFrame("Let me transfer you to someone who can help.")])
        await _initiate_transfer(pipeline)


def setup_output_validator_handlers(pipeline, output_validator, config):
    @output_validator.event_handler("on_unsafe_output")
    async def handle_unsafe_output(processor, text):
        logger.warning(f"UNSAFE output detected - session {pipeline.session_id}: {text[:100]}...")
        msg = config.get("unsafe_output_message", "I apologize, let me rephrase that.")
        await pipeline.task.queue_frames([TTSSpeakFrame(msg)])
