"""Triage event handlers - connect classification to flow and IVR navigation."""

from datetime import datetime
from loguru import logger

from pipecat.frames.frames import EndFrame, TTSSpeakFrame, VADParamsUpdateFrame
from pipecat.audio.vad.vad_analyzer import VADParams

from backend.models import get_async_patient_db
from backend.constants import CallStatus
from pipeline.ivr_navigation_processor import IVRStatus


def setup_triage_handlers(
    pipeline,
    triage_detector,
    ivr_processor,
    flow,
    flow_manager,
):
    """Configure event handlers for triage classification.

    Args:
        pipeline: CallSession with transcripts, patient_id, etc.
        triage_detector: TriageDetector parallel pipeline
        ivr_processor: IVRNavigationProcessor for menu navigation
        flow: Client flow instance with get_triage_config(), create_greeting_node_*()
        flow_manager: FlowManager for initializing conversation nodes
    """

    @triage_detector.event_handler("on_conversation_detected")
    async def handle_conversation(processor, conversation_history):
        """Human answered - start conversation flow."""
        logger.info("TRIAGE: Human detected - starting conversation")

        # 0.8s for natural conversation pace; increase if interruptions occur
        await pipeline.task.queue_frames([
            VADParamsUpdateFrame(VADParams(stop_secs=0.8))
        ])

        greeting_node = flow.create_greeting_node_without_ivr()

        # Inject the last utterance heard into the node's task_messages
        # so the LLM knows what the person said when they answered
        if conversation_history:
            last_utterance = conversation_history[-1].get("content", "")
            if last_utterance:
                user_msg = {"role": "user", "content": last_utterance}
                greeting_node["task_messages"].append(user_msg)
                logger.info(f"Injected utterance: {last_utterance[:50]}...")

        await flow_manager.initialize(greeting_node)

        pipeline.transcripts.append({
            "role": "system",
            "content": "Human answered - starting conversation",
            "timestamp": datetime.now().isoformat(),
            "type": "triage"
        })

    @triage_detector.event_handler("on_ivr_detected")
    async def handle_ivr(processor, conversation_history):
        """IVR detected - start navigation."""
        logger.info("TRIAGE: IVR detected - starting navigation")

        pipeline.transcripts.append({
            "role": "system",
            "content": "IVR system detected - navigating menus",
            "timestamp": datetime.now().isoformat(),
            "type": "triage"
        })

        triage_config = flow.get_triage_config()
        ivr_goal = triage_config.get("ivr_navigation_goal", "")

        await ivr_processor.activate(ivr_goal, conversation_history)

    @triage_detector.event_handler("on_voicemail_detected")
    async def handle_voicemail(processor):
        """Voicemail detected - leave message and end call."""
        logger.info("TRIAGE: Voicemail detected - leaving message")

        pipeline.transcripts.append({
            "role": "system",
            "content": "Voicemail detected - leaving message",
            "timestamp": datetime.now().isoformat(),
            "type": "triage"
        })

        await get_async_patient_db().update_call_status(
            pipeline.patient_id,
            CallStatus.VOICEMAIL.value,
            pipeline.organization_id
        )

        triage_config = flow.get_triage_config()
        voicemail_message = triage_config.get("voicemail_message", "")

        if voicemail_message:
            await processor.push_frame(TTSSpeakFrame(voicemail_message))

        await pipeline.task.queue_frames([EndFrame()])

    @ivr_processor.event_handler("on_ivr_status_changed")
    async def handle_ivr_status(processor, status):
        """Handle IVR navigation completion or failure."""

        if status == IVRStatus.COMPLETED:
            logger.info("TRIAGE: IVR navigation completed - starting conversation")

            pipeline.transcripts.append({
                "role": "system",
                "content": "IVR navigation completed",
                "timestamp": datetime.now().isoformat(),
                "type": "triage"
            })

            triage_detector.notify_ivr_completed()

            # 0.8s for natural conversation pace; increase if interruptions occur
            await pipeline.task.queue_frames([
                VADParamsUpdateFrame(VADParams(stop_secs=0.8))
            ])

            greeting_node = flow.create_greeting_node_after_ivr_completed()
            await flow_manager.initialize(greeting_node)

            # Explicitly trigger LLM to generate greeting
            messages = pipeline.context.get_messages()
            await pipeline.context_aggregator.assistant().push_frame(
                LLMMessagesUpdateFrame(messages=messages, run_llm=True),
                FrameDirection.UPSTREAM
            )

        elif status == IVRStatus.STUCK:
            logger.error("TRIAGE: IVR navigation stuck - ending call")

            pipeline.transcripts.append({
                "role": "system",
                "content": "IVR navigation failed",
                "timestamp": datetime.now().isoformat(),
                "type": "triage"
            })

            await get_async_patient_db().update_call_status(
                pipeline.patient_id,
                CallStatus.FAILED.value,
                pipeline.organization_id
            )

            await pipeline.task.queue_frames([EndFrame()])

    @ivr_processor.event_handler("on_dtmf_pressed")
    async def handle_dtmf(processor, value):
        """Log DTMF keypress to transcript."""
        pipeline.transcripts.append({
            "role": "assistant",
            "content": f"Pressed {value}",
            "timestamp": datetime.now().isoformat(),
            "type": "ivr_action"
        })
