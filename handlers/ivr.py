import re
import logging
from datetime import datetime
from pipecat.frames.frames import Frame, TextFrame, VADParamsUpdateFrame, EndFrame, ManuallySwitchServiceFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.extensions.ivr.ivr_navigator import IVRStatus
from backend.models import get_async_patient_db

logger = logging.getLogger(__name__)


class IVRTranscriptProcessor(FrameProcessor):

    def __init__(self, transcripts_list, **kwargs):
        super().__init__(**kwargs)
        self._transcripts = transcripts_list

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame) and hasattr(frame, 'skip_tts') and frame.skip_tts:
            content = frame.text
            dtmf_match = re.search(r'<dtmf>(\d+)</dtmf>', content)
            if dtmf_match:
                self._transcripts.append({
                    "role": "assistant",
                    "content": f"Pressed {dtmf_match.group(1)}",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_action"
                })

        await self.push_frame(frame, direction)


def setup_ivr_handlers(pipeline, ivr_navigator):
    @ivr_navigator.event_handler("on_conversation_detected")
    async def on_conversation_detected(processor, conversation_history):
        """Human answered - transition to greeting node.

        Note: LLM switching is handled by greeting node's pre-action.
        """
        try:
            await pipeline.task.queue_frames([
                VADParamsUpdateFrame(VADParams(stop_secs=0.8))
            ])

            # Inject the detected user utterance into the greeting context
            greeting_node = pipeline.flow.create_greeting_node()

            # If we have conversation history, append the last user message to the greeting node
            # NodeConfig is a dictionary, so we use key access instead of attribute access
            if conversation_history:
                # Extract the string content from the last user message
                last_content = conversation_history[-1]['content'] if conversation_history else ""
                user_msg = {"role": "user", "content": last_content}

                greeting_node['task_messages'].append(user_msg)

                # Log the truncated string safely
                logger.info(f"✅ Injected user utterance into greeting: {last_content[:50]}...")

            await pipeline.flow_manager.initialize(greeting_node)

            logger.info("✅ Human detected → greeting")

        except Exception as e:
            logger.error(f"❌ Error in conversation handler: {e}")
            raise

    @ivr_navigator.event_handler("on_ivr_status_changed")
    async def on_ivr_status_changed(processor, status):
        """Handle IVR navigation status changes.

        Note: LLM switching for navigation is handled by IVRNavigator's internal logic.
        LLM switching for conversation is handled by node pre-actions.
        """
        try:
            if status == IVRStatus.DETECTED:
                # IVRNavigator will use main_llm for navigation (configured in pipeline_factory)
                logger.info("✅ IVR detected → navigating")

                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR system detected - navigating menus",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

            elif status == IVRStatus.COMPLETED:
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR navigation completed",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                await pipeline.task.queue_frames([
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                ])

                greeting_node = pipeline.flow.create_greeting_node()
                await pipeline.flow_manager.initialize(greeting_node)

                logger.info("✅ IVR complete → greeting")

            elif status == IVRStatus.STUCK:
                logger.error("❌ IVR navigation failed - ending call")

                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR navigation failed",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                await get_async_patient_db().update_call_status(pipeline.patient_id, "Failed")
                await pipeline.task.queue_frames([EndFrame()])

        except Exception as e:
            logger.error(f"❌ Error in IVR status handler: {e}")
