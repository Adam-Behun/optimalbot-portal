"""Daily.co transport event handlers"""

from datetime import datetime
from loguru import logger
from pipecat.frames.frames import EndFrame
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


def setup_dialout_handlers(pipeline):
    """Setup Daily dial-out event handlers"""

    @pipeline.transport.event_handler("on_joined")
    async def on_joined(transport, data):
        logger.info(f"✅ Bot joined Daily room, dialing {pipeline.phone_number}")

        try:
            await transport.start_dialout({"phoneNumber": pipeline.phone_number})
        except Exception as e:
            logger.error(f"❌ Dial-out failed: {e}")

    @pipeline.transport.event_handler("on_dialout_answered")
    async def on_dialout_answered(transport, data):
        # Check if this is a transfer completion or initial call answer
        if pipeline.transfer_in_progress:
            logger.info("✅ Supervisor transfer completed")

            # Add transfer event to transcript
            pipeline.transcripts.append({
                "role": "system",
                "content": "Call transferred to supervisor",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })

            # Update call status
            await get_async_patient_db().update_call_status(
                pipeline.patient_id,
                "Call Transferred"
            )

            # Save transcript before bot exits
            await save_transcript_to_db(pipeline)

            # Bot leaves call (cold transfer)
            await pipeline.task.queue_frames([EndFrame()])

        else:
            # Initial call answered
            logger.info(f"✅ Call answered by {pipeline.phone_number}")

    @pipeline.transport.event_handler("on_dialout_stopped")
    async def on_dialout_stopped(transport, data):
        """Handle dialout stopped - save transcript and end pipeline gracefully."""
        await save_transcript_to_db(pipeline)

        # Signal pipeline to end gracefully
        if pipeline.task:
            await pipeline.task.queue_frames([EndFrame()])

    @pipeline.transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, data):
        """Handle participant leaving - update status, save transcript, end pipeline gracefully."""
        # Update call status if not already terminal
        try:
            patient = await get_async_patient_db().find_patient_by_id(pipeline.patient_id)
            current_status = patient.get("call_status") if patient else None

            if current_status not in ["Completed", "Completed - Left VM", "Failed"]:
                await get_async_patient_db().update_call_status(pipeline.patient_id, "Completed")
                logger.info("✅ Call ended - Status: Completed")
        except Exception as e:
            logger.error(f"❌ Error updating call status: {e}")

        # Save transcript before terminating
        await save_transcript_to_db(pipeline)

        # Signal pipeline to end gracefully (EndFrame will trigger cleanup)
        if pipeline.task:
            await pipeline.task.queue_frames([EndFrame()])

    @pipeline.transport.event_handler("on_dialout_error")
    async def on_dialout_error(transport, data):
        # Check if this is a transfer error or initial dialout error
        if pipeline.transfer_in_progress:
            logger.error(f"❌ Supervisor transfer failed - continuing call")

            # Reset transfer flag
            pipeline.transfer_in_progress = False

            # Add error event to transcript
            pipeline.transcripts.append({
                "role": "system",
                "content": "Transfer to supervisor failed",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })

            # Don't terminate - call continues with insurance rep

        else:
            # Initial dialout failed
            logger.error(f"❌ Call failed - Dialout error: {data}")
            await get_async_patient_db().update_call_status(pipeline.patient_id, "Failed")

            # Save transcript even on error (may have partial conversation)
            await save_transcript_to_db(pipeline)

            # Signal pipeline to end gracefully
            if pipeline.task:
                await pipeline.task.queue_frames([EndFrame()])