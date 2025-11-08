import re
from datetime import datetime
from loguru import logger
from pipecat.frames.frames import (
    LLMMessagesUpdateFrame,
    VADParamsUpdateFrame,
    TTSSpeakFrame,
    EndFrame,
    ManuallySwitchServiceFrame
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.extensions.ivr.ivr_navigator import IVRStatus
from backend.models import get_async_patient_db
from backend.functions import PATIENT_TOOLS

def _process_ivr_conversation(conversation_history, pipeline):
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
    pipeline.ivr_navigator = ivr_navigator

    logger.info(f"🔧 IVR handlers configured for session: {pipeline.session_id}")

    @ivr_navigator.event_handler("on_conversation_detected")
    async def on_conversation_detected(processor, conversation_history):
        try:
            logger.info(f"👤 Human detected - Session: {pipeline.session_id}")
            logger.debug(f"Conversation history length: {len(conversation_history) if conversation_history else 0}")

            _process_ivr_conversation(conversation_history, pipeline)

            # Optional: Log human's initial greeting for debugging
            if conversation_history:
                last_msg = conversation_history[-1].get('content', '')
                logger.debug(f"Human said: {last_msg}")

            # Set faster VAD for human conversation
            await pipeline.task.queue_frames([
                VADParamsUpdateFrame(VADParams(stop_secs=0.8))
            ])

            # Transition to greeting state (StateManager handles: LLM switching, tools, and greeting generation)
            await pipeline.state_manager.transition_to("greeting", "human_answered")

            logger.info("✅ Greeting transition complete")

        except Exception as e:
            logger.error(f"❌ Error in conversation handler: {e}")
            raise
    
    @ivr_navigator.event_handler("on_ivr_status_changed")
    async def on_ivr_status_changed(processor, status):
        """Handle IVR navigation status changes"""
        try:
            if status == IVRStatus.DETECTED:
                logger.info("IVR system detected - auto-navigation starting")
                switch_frame = ManuallySwitchServiceFrame(service=pipeline.main_llm)
                await pipeline.context_aggregators.assistant().push_frame(
                    switch_frame, FrameDirection.UPSTREAM
                )

                logger.info("✅ Switched to main LLM for IVR navigation")

                # Add IVR detection summary to transcript
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR system detected - navigating automatically",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

            elif status == IVRStatus.COMPLETED:
                logger.info("✅ IVR navigation complete - human reached")

                pipeline.transcripts.append({
                    "role": "system",
                    "content": "Completed",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                await pipeline.task.queue_frames([
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                ])

                await pipeline.state_manager.transition_to("greeting", "ivr_complete")

                logger.info("✅ Greeting transition complete after IVR - <1s response achieved")
            
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