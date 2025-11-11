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
        """Human answered - stay on classifier_llm and transition to greeting node."""
        try:
            await pipeline.context_aggregator.assistant().push_frame(
                ManuallySwitchServiceFrame(service=pipeline.flow.classifier_llm),
                FrameDirection.UPSTREAM
            )

            await pipeline.task.queue_frames([
                VADParamsUpdateFrame(VADParams(stop_secs=0.8))
            ])

            greeting_node = pipeline.flow.create_greeting_node()
            await pipeline.flow_manager.initialize(greeting_node)

            logger.info("✅ Human detected → greeting (classifier_llm)")

        except Exception as e:
            logger.error(f"❌ Error in conversation handler: {e}")
            raise

    @ivr_navigator.event_handler("on_ivr_status_changed")
    async def on_ivr_status_changed(processor, status):
        """Handle IVR navigation status changes with LLM switching."""
        try:
            if status == IVRStatus.DETECTED:
                # SWITCH TO MAIN_LLM for complex IVR navigation
                await pipeline.context_aggregator.assistant().push_frame(
                    ManuallySwitchServiceFrame(service=pipeline.flow.main_llm),
                    FrameDirection.UPSTREAM
                )

                # Update IVRNavigator's internal LLM reference so it uses main_llm for navigation
                pipeline.ivr_navigator._llm = pipeline.flow.main_llm

                logger.info("✅ IVR detected → navigating (main_llm)")

                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR system detected - switched to main_llm for navigation",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

            elif status == IVRStatus.COMPLETED:
                # SWITCH BACK TO CLASSIFIER_LLM for greeting
                await pipeline.context_aggregator.assistant().push_frame(
                    ManuallySwitchServiceFrame(service=pipeline.flow.classifier_llm),
                    FrameDirection.UPSTREAM
                )

                # Update IVRNavigator's internal LLM reference back to classifier_llm
                pipeline.ivr_navigator._llm = pipeline.flow.classifier_llm

                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR navigation completed - switched to classifier_llm",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                await pipeline.task.queue_frames([
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                ])

                greeting_node = pipeline.flow.create_greeting_node()
                await pipeline.flow_manager.initialize(greeting_node)

                logger.info("✅ IVR complete → greeting (classifier_llm)")

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
