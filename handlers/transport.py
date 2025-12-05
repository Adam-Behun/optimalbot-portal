"""Daily.co transport event handlers"""

from datetime import datetime
from loguru import logger
from pipecat.frames.frames import EndFrame
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


def setup_transport_handlers(pipeline, call_type: str):
    """Setup transport handlers based on call type."""
    if call_type == "dial-in":
        setup_dialin_handlers(pipeline)
    else:
        setup_dialout_handlers(pipeline)


def setup_dialin_handlers(pipeline):
    """Setup Daily dial-in event handlers."""

    @pipeline.transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        """Handle when caller connects - initialize flow and bot speaks first."""
        logger.info(f"✅ Caller connected: {participant['id']}")

        # Start capturing caller's audio for transcription
        await transport.capture_participant_transcription(participant["id"])

        # Initialize flow with greeting node (respond_immediately=True makes bot speaks first)
        if pipeline.flow and pipeline.flow_manager:
            initial_node = pipeline.flow.create_greeting_node()
            await pipeline.flow_manager.initialize(initial_node)
            logger.info("✅ Flow initialized with greeting node")

    @pipeline.transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        """Handle client disconnect - save transcript and cleanup."""
        logger.info("✅ Caller disconnected")

        try:
            # Update call status
            patient = await get_async_patient_db().find_patient_by_id(
                pipeline.patient_id, pipeline.organization_id
            )
            current_status = patient.get("call_status") if patient else None

            if current_status not in ["Completed", "Failed", "Transferred"]:
                await get_async_patient_db().update_call_status(
                    pipeline.patient_id, "Completed", pipeline.organization_id
                )
                logger.info("✅ Database status updated: Completed (client disconnected)")
        except Exception as e:
            logger.error(f"❌ Error updating call status on client disconnect: {e}")

        # Save transcript
        await save_transcript_to_db(pipeline)

        # Cancel task
        if pipeline.task:
            await pipeline.task.cancel()
            logger.info("✅ Pipeline cancelled (client disconnected)")

    @pipeline.transport.event_handler("on_dialin_error")
    async def on_dialin_error(transport, data):
        """Handle dial-in error."""
        logger.error(f"❌ Dial-in error: {data}")

        try:
            await get_async_patient_db().update_call_status(
                pipeline.patient_id, "Failed", pipeline.organization_id
            )
            logger.info("✅ Database status updated: Failed")
        except Exception as e:
            logger.error(f"❌ Error updating call status on dialin error: {e}")

        # Save transcript
        await save_transcript_to_db(pipeline)

        # Cancel task
        if pipeline.task:
            await pipeline.task.cancel()
            logger.info("✅ Pipeline cancelled (dialin error)")

    @pipeline.transport.event_handler("on_dialout_answered")
    async def on_dialout_answered(transport, data):
        """Handle cold transfer completion - staff answered, bot exits."""
        if pipeline.transfer_in_progress:
            logger.info("✅ Cold transfer completed - staff answered")

            pipeline.transcripts.append({
                "role": "system",
                "content": "Call transferred to staff",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })

            await get_async_patient_db().update_call_status(
                pipeline.patient_id, "Transferred", pipeline.organization_id
            )
            logger.info("✅ Database status updated: Transferred")

            await save_transcript_to_db(pipeline)

            await pipeline.task.queue_frames([EndFrame()])
            logger.info("✅ EndFrame queued - bot will exit after transfer completes")

    @pipeline.transport.event_handler("on_dialout_error")
    async def on_dialout_error(transport, data):
        """Handle cold transfer failure - reset flag and continue call."""
        if pipeline.transfer_in_progress:
            logger.error(f"❌ Cold transfer failed: {data}")

            pipeline.transfer_in_progress = False

            pipeline.transcripts.append({
                "role": "system",
                "content": "Transfer to staff failed",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })

            logger.info("✅ Call continuing with patient")


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
        # Check for cold transfer (supervisor - bot exits immediately)
        if pipeline.transfer_in_progress:
            logger.info("✅ Supervisor transfer completed")

            pipeline.transcripts.append({
                "role": "system",
                "content": "Call transferred to supervisor",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })

            await get_async_patient_db().update_call_status(
                pipeline.patient_id, "Supervisor Dialed", pipeline.organization_id
            )
            logger.info("✅ Database status updated: Supervisor Dialed")

            await save_transcript_to_db(pipeline)

            await pipeline.task.queue_frames([EndFrame()])
            logger.info("✅ EndFrame queued - bot will exit after transfer completes")

        else:
            # Initial call answered
            logger.info(f"✅ Call answered by {pipeline.phone_number}")

    @pipeline.transport.event_handler("on_dialout_stopped")
    async def on_dialout_stopped(transport, data):
        """Handle dialout stopped - update status based on current state, save transcript, and terminate immediately."""
        try:
            # Check current status to determine appropriate final status
            patient = await get_async_patient_db().find_patient_by_id(
                pipeline.patient_id, pipeline.organization_id
            )
            current_status = patient.get("call_status") if patient else None

            # Only update if not already in a terminal state
            if current_status not in ["Completed", "Supervisor Dialed", "Failed"]:
                await get_async_patient_db().update_call_status(
                    pipeline.patient_id, "Completed", pipeline.organization_id
                )
                logger.info("✅ Database status updated: Completed (dialout stopped)")
            else:
                logger.info(f"✅ Call status already terminal: {current_status}")

        except Exception as e:
            logger.error(f"❌ Error updating call status on dialout stopped: {e}")

        # Save transcript before terminating
        await save_transcript_to_db(pipeline)

        # Immediate termination - user already disconnected
        if pipeline.task:
            await pipeline.task.cancel()
            logger.info("✅ Pipeline cancelled immediately (dialout stopped)")

    @pipeline.transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, data):
        """Handle participant leaving - update status based on current state, save transcript, and terminate immediately."""
        try:
            # Check current status to determine appropriate final status
            patient = await get_async_patient_db().find_patient_by_id(
                pipeline.patient_id, pipeline.organization_id
            )
            current_status = patient.get("call_status") if patient else None

            # Only update if not already in a terminal state
            if current_status not in ["Completed", "Supervisor Dialed", "Failed"]:
                await get_async_patient_db().update_call_status(
                    pipeline.patient_id, "Completed", pipeline.organization_id
                )
                logger.info("✅ Database status updated: Completed (participant left)")
            else:
                logger.info(f"✅ Call status already terminal: {current_status}")

        except Exception as e:
            logger.error(f"❌ Error updating call status on participant left: {e}")

        # Save transcript before terminating
        await save_transcript_to_db(pipeline)

        # Immediate termination - user already gone, no need to complete pending frames
        if pipeline.task:
            await pipeline.task.cancel()
            logger.info("✅ Pipeline cancelled immediately (participant left)")

    @pipeline.transport.event_handler("on_dialout_error")
    async def on_dialout_error(transport, data):
        # Check for cold transfer error
        if pipeline.transfer_in_progress:
            logger.error("❌ Supervisor transfer failed - continuing call")

            pipeline.transfer_in_progress = False

            pipeline.transcripts.append({
                "role": "system",
                "content": "Transfer to supervisor failed",
                "timestamp": datetime.utcnow().isoformat(),
                "type": "transfer"
            })

            logger.info("✅ Call continuing with insurance representative")

        else:
            # Initial dialout failed - call never connected
            logger.error(f"❌ Call failed - Dialout error: {data}")

            await get_async_patient_db().update_call_status(
                pipeline.patient_id, "Failed", pipeline.organization_id
            )
            logger.info("✅ Database status updated: Failed")

            await save_transcript_to_db(pipeline)

            if pipeline.task:
                await pipeline.task.cancel()
                logger.info("✅ Pipeline cancelled immediately (dialout error)")