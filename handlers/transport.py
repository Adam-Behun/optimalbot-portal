"""Daily.co transport event handlers"""

from loguru import logger
from backend.models import get_async_patient_db
from monitoring import emit_event


def setup_dialout_handlers(pipeline):
    """Setup Daily dial-out event handlers"""
    
    @pipeline.transport.event_handler("on_joined")
    async def on_joined(transport, data):
        logger.info(f"Bot joined, dialing {pipeline.phone_number}")
        
        try:
            await transport.start_dialout({"phoneNumber": pipeline.phone_number})
            emit_event(
                session_id=pipeline.session_id,
                category="CALL",
                event="dialout_initiated",
                metadata={"phone_number": pipeline.phone_number}
            )
        except Exception as e:
            logger.error(f"Dial-out failed: {e}")
            emit_event(
                session_id=pipeline.session_id,
                category="CALL",
                event="dialout_failed",
                severity="error",
                metadata={"phone_number": pipeline.phone_number, "error": str(e)}
            )
    
    @pipeline.transport.event_handler("on_dialout_answered")
    async def on_dialout_answered(transport, data):
        logger.info(f"Call answered: {pipeline.phone_number}")
        emit_event(
            session_id=pipeline.session_id,
            category="CALL",
            event="dialout_answered",
            metadata={"phone_number": pipeline.phone_number}
        )
    
    @pipeline.transport.event_handler("on_dialout_stopped")
    async def on_dialout_stopped(transport, data):
        logger.info("Call ended")
        emit_event(
            session_id=pipeline.session_id,
            category="CALL",
            event="dialout_stopped",
            metadata={"phone_number": pipeline.phone_number}
        )
        
        # Terminate pipeline
        if pipeline.task:
            await pipeline.task.cancel()
    
    @pipeline.transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, data):
        logger.info(f"Participant left: {participant}")
        
        emit_event(
            session_id=pipeline.session_id,
            category="CALL",
            event="participant_left",
            metadata={"participant_id": participant}
        )
        
        # Update call status if not already terminal
        try:
            patient = await get_async_patient_db().find_patient_by_id(pipeline.patient_id)
            current_status = patient.get("call_status") if patient else None
            
            if current_status not in ["Completed", "Completed - Left VM", "Failed"]:
                await get_async_patient_db().update_call_status(pipeline.patient_id, "Completed")
                logger.info("✅ Call status: Completed")
        except Exception as e:
            logger.error(f"Error updating call status: {e}")
        
        # Terminate pipeline
        if pipeline.task:
            await pipeline.task.cancel()
    
    @pipeline.transport.event_handler("on_dialout_error")
    async def on_dialout_error(transport, data):
        logger.error(f"Dialout error: {data}")
        
        await get_async_patient_db().update_call_status(pipeline.patient_id, "Failed")
        
        emit_event(
            session_id=pipeline.session_id,
            category="CALL",
            event="dialout_error",
            severity="error",
            metadata={"phone_number": pipeline.phone_number, "error_data": data}
        )
        
        # Terminate pipeline
        if pipeline.task:
            await pipeline.task.cancel()