import re
from datetime import datetime
from loguru import logger
from pipecat.frames.frames import (
    LLMMessagesUpdateFrame,
    VADParamsUpdateFrame,
    TTSSpeakFrame,
    EndFrame
)
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.extensions.ivr.ivr_navigator import IVRStatus
from backend.models import get_async_patient_db
from backend.functions import PATIENT_TOOLS

# CRITICAL: Pre-define greeting as constant for zero LLM latency
HUMAN_GREETING = "Hi, this is Alexandra from Adam's Medical Practice. I'm calling to verify eligibility and benefits for a patient."


def _process_ivr_conversation(conversation_history, pipeline):
    """
    Process IVR conversation history and add to transcript.
    Extracts DTMF selections and logs them as "Pressed X".

    Args:
        conversation_history: List of messages from IVRNavigator
        pipeline: Pipeline object with transcripts list
    """
    if not conversation_history:
        return

    for msg in conversation_history:
        content = msg.get('content', '')
        role = msg.get('role', 'assistant')

        # Extract DTMF tags (e.g., <dtmf>2</dtmf>)
        dtmf_match = re.search(r'<dtmf>(\d+)</dtmf>', content)

        if dtmf_match:
            # Log DTMF selection explicitly
            pipeline.transcripts.append({
                "role": "system",
                "content": f"Pressed {dtmf_match.group(1)}",
                "timestamp": datetime.now().isoformat(),
                "type": "ivr_action"
            })

            # Also add clean content without DTMF tags if any
            clean_content = re.sub(r'<dtmf>\d+</dtmf>', '', content).strip()
            if clean_content:
                pipeline.transcripts.append({
                    "role": role,
                    "content": clean_content,
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr"
                })
        else:
            # Regular IVR message (menu prompts, verbal responses)
            pipeline.transcripts.append({
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "type": "ivr"
            })


def setup_ivr_handlers(pipeline, ivr_navigator):
    """Setup IVRNavigator event handlers for <1s response time"""
    pipeline.ivr_navigator = ivr_navigator
    
    @ivr_navigator.event_handler("on_conversation_detected")
    async def on_conversation_detected(processor, conversation_history):
        """
        Fires IMMEDIATELY when human detected (not after full processing).
        This is where we achieve <1s response time.
        """
        try:
            logger.info(f"👤 Human detected - Session: {pipeline.session_id}")

            # Save IVR conversation to transcript (for DB/frontend)
            # This does NOT get passed to LLM - keeps conversation context clean
            _process_ivr_conversation(conversation_history, pipeline)

            # Optional: Log human's initial greeting for debugging
            if conversation_history:
                last_msg = conversation_history[-1].get('content', '')
                logger.debug(f"Human said: {last_msg}")

            # OPTIMIZATION: Skip greeting state, go straight to verification
            pipeline.conversation_context.transition_to("verification", "human_answered")
            verification_prompt = pipeline.conversation_context.render_prompt()

            # Enable tools for function calling
            context = pipeline.context_aggregators.user().context
            context.set_tools(PATIENT_TOOLS)

            # Build LLM messages - START FRESH without IVR conversation_history
            # Only include clean verification prompt
            messages = [{"role": "system", "content": verification_prompt}]

            # CRITICAL: Queue frames in this order for optimal performance
            await pipeline.task.queue_frames([
                VADParamsUpdateFrame(VADParams(stop_secs=0.8)),  # 1. Set faster VAD FIRST
                TTSSpeakFrame(HUMAN_GREETING),                    # 2. Queue TTS directly (no LLM)
                LLMMessagesUpdateFrame(messages=messages, run_llm=False)  # 3. Setup context; don't run LLM yet - let human respond to greeting first
            ])
            
            logger.info("✅ Greeting queued successfully - <1s response achieved")
            
        except Exception as e:
            logger.error(f"❌ Error in conversation handler: {e}")
            # Fallback: still try to greet
            await pipeline.task.queue_frames([TTSSpeakFrame(HUMAN_GREETING)])
    
    @ivr_navigator.event_handler("on_ivr_status_changed")
    async def on_ivr_status_changed(processor, status):
        """Handle IVR navigation status changes"""
        try:
            if status == IVRStatus.DETECTED:
                logger.info("🤖 IVR system detected - auto-navigation starting")

                # Add IVR detection summary to transcript
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR system detected - navigating automatically",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

            elif status == IVRStatus.COMPLETED:
                logger.info("✅ IVR navigation complete - human reached")

                # Add IVR completion summary to transcript
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "Completed",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                # Same fast greeting flow as direct human detection
                pipeline.conversation_context.transition_to("verification", "ivr_complete")
                verification_prompt = pipeline.conversation_context.render_prompt()

                # Enable tools for function calling
                context = pipeline.context_aggregators.user().context
                context.set_tools(PATIENT_TOOLS)

                messages = [{"role": "system", "content": verification_prompt}]

                await pipeline.task.queue_frames([
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8)),
                    TTSSpeakFrame(HUMAN_GREETING),
                    LLMMessagesUpdateFrame(messages=messages, run_llm=False)  # Setup context; don't run LLM yet
                ])
                
                logger.info("✅ Greeting queued after IVR - <1s response achieved")
            
            elif status == IVRStatus.STUCK:
                logger.warning("⚠️ IVR navigation stuck - ending call")

                # Add IVR stuck summary to transcript
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "Failed - navigation stuck",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                pipeline.conversation_context.transition_to("ivr_stuck", "navigation_failed")

                await get_async_patient_db().update_call_status(pipeline.patient_id, "Failed")
                await pipeline.task.queue_frames([EndFrame()])

                logger.info("❌ Call ended - IVR stuck")
        
        except Exception as e:
            logger.error(f"❌ Error in IVR status handler: {e}")