from loguru import logger
from pipecat.frames.frames import LLMMessagesUpdateFrame, VADParamsUpdateFrame, TTSSpeakFrame, EndFrame
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.extensions.ivr.ivr_navigator import IVRStatus
from models import get_async_patient_db
from monitoring import emit_event

def setup_ivr_handlers(pipeline, ivr_navigator):
    """Setup IVRNavigator event handlers"""
    pipeline.ivr_navigator = ivr_navigator
    
    @ivr_navigator.event_handler("on_conversation_detected")
    async def on_conversation_detected(processor, conversation_history):
        logger.info(f"👤 Human answered - Session: {pipeline.session_id}")
        
        emit_event(
            session_id=pipeline.session_id,
            category="DETECTION",
            event="conversation_detected",
            metadata={"phone_number": pipeline.phone_number}
        )
        
        # Transition state to get proper greeting prompt
        pipeline.conversation_context.transition_to("greeting", "human_answered_directly")
        greeting_prompt = pipeline.conversation_context.render_prompt()
        
        # Build messages with system prompt
        messages = [{"role": "system", "content": greeting_prompt}]
        
        # Add conversation history if available
        if conversation_history:
            messages.extend(conversation_history)
        
        # Update context and start conversation
        if pipeline.task:
            await pipeline.task.queue_frames([
                LLMMessagesUpdateFrame(messages=messages, run_llm=True),
                VADParamsUpdateFrame(VADParams(stop_secs=0.8))
            ])
        
        logger.info("✅ Conversation started")
    
    @ivr_navigator.event_handler("on_ivr_status_changed")
    async def on_ivr_status_changed(processor, status):
        logger.info(f"🤖 IVR Status: {status}")
        
        emit_event(
            session_id=pipeline.session_id,
            category="DETECTION",
            event="ivr_status_changed",
            metadata={"status": str(status), "phone_number": pipeline.phone_number}
        )
        
        if status == IVRStatus.DETECTED:
            logger.info("✅ IVR system detected - navigation beginning automatically")
            # IVR Navigator handles this automatically - no action needed
        
        elif status == IVRStatus.COMPLETED:
            logger.info("✅ IVR navigation completed")
            
            # Transition to greeting state
            pipeline.conversation_context.transition_to("greeting", "ivr_navigation_complete")
            greeting_prompt = pipeline.conversation_context.render_prompt()
            
            # Set up conversation with proper prompt
            messages = [{"role": "system", "content": greeting_prompt}]
            
            if pipeline.task:
                await pipeline.task.queue_frames([
                    LLMMessagesUpdateFrame(messages=messages, run_llm=True),
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                ])
            
            logger.info("✅ IVR completed, conversation started")
        
        elif status == IVRStatus.STUCK:
            logger.warning("⚠️ IVR navigation stuck - terminating call")
            
            pipeline.conversation_context.transition_to("ivr_stuck", "ivr_navigation_failed")
            
            # Update database
            await get_async_patient_db().update_call_status(pipeline.patient_id, "Failed")
            
            # Terminate
            if pipeline.task:
                await pipeline.task.cancel()
            
            logger.info("❌ Call terminated - IVR stuck")