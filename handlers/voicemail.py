"""Voicemail detection and handling"""

from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame, EndFrame
from backend.models import get_async_patient_db
from monitoring import add_span_attributes


def setup_voicemail_handlers(pipeline, voicemail_detector):
    """Setup VoicemailDetector event handlers"""
    pipeline.voicemail_detector = voicemail_detector
    
    @voicemail_detector.event_handler("on_voicemail_detected")
    async def handle_voicemail(processor):
        logger.info(f"📞 Voicemail detected - Session: {pipeline.session_id}")
        
        # Add span attributes for voicemail detection
        add_span_attributes(
            **{
                "detection.type": "voicemail",
                "detection.phone_number": pipeline.phone_number,
                "conversation.state": "voicemail_detected",
            }
        )
        
        # Transition to voicemail state
        await pipeline.state_manager.transition_to("voicemail_detected", "voicemail_system_detected")
        
        # Get voicemail message from schema
        voicemail_prompt = pipeline.conversation_schema.prompts.get("voicemail_detected", {})
        message = voicemail_prompt.get("message", "")
        
        if message:
            message = pipeline.prompt_renderer.render_template(message, pipeline.patient_data)
        else:
            message = "Hello, this was Alexandra trying to reach out regarding eligibility and benefits verification. Thank you."

        await processor.push_frame(TTSSpeakFrame(message))
        await get_async_patient_db().update_call_status(pipeline.patient_id, "Completed - Left VM")
        
        if pipeline.task:
            await pipeline.task.queue_frames([EndFrame()])
        
        logger.info("✅ Voicemail left, call ending")