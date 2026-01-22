import asyncio
from typing import List, Optional

from loguru import logger


# =============================================================================
# CONSTANTS - Used by evals to ensure sync with production
# =============================================================================

class TriageClassification:
    """Classification output values from classifier LLM."""
    CONVERSATION = "CONVERSATION"
    IVR = "IVR"
    VOICEMAIL = "VOICEMAIL"


class TriageEvent:
    """Events fired by triage processors."""
    CONVERSATION_DETECTED = "on_conversation_detected"
    IVR_DETECTED = "on_ivr_detected"
    VOICEMAIL_DETECTED = "on_voicemail_detected"


CLASSIFICATION_TO_EVENT = {
    TriageClassification.CONVERSATION: TriageEvent.CONVERSATION_DETECTED,
    TriageClassification.IVR: TriageEvent.IVR_DETECTED,
    TriageClassification.VOICEMAIL: TriageEvent.VOICEMAIL_DETECTED,
}

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StopFrame,
    SystemFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup
from pipecat.utils.sync.base_notifier import BaseNotifier


class MainBranchGate(FrameProcessor):
    """Blocks main pipeline until CONVERSATION detected, IVR detected, or IVR navigation completed.

    Starts closed. Opens when any of:
    - conversation_notifier signals (human answered)
    - ivr_notifier signals (IVR detected - need to pass transcriptions to IVR navigator)
    - ivr_completed_notifier signals (IVR navigation finished)

    When closed, only SystemFrame, EndFrame, StopFrame pass through.
    """

    def __init__(
        self,
        conversation_notifier: BaseNotifier,
        ivr_notifier: BaseNotifier,
        ivr_completed_notifier: BaseNotifier,
    ):
        super().__init__()
        self._conversation_notifier = conversation_notifier
        self._ivr_notifier = ivr_notifier
        self._ivr_completed_notifier = ivr_completed_notifier
        self._gate_open = False
        self._conversation_task: Optional[asyncio.Task] = None
        self._ivr_task: Optional[asyncio.Task] = None
        self._ivr_completed_task: Optional[asyncio.Task] = None

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        self._conversation_task = self.create_task(self._wait_for_conversation())
        self._ivr_task = self.create_task(self._wait_for_ivr())
        self._ivr_completed_task = self.create_task(self._wait_for_ivr_completed())

    async def cleanup(self):
        await super().cleanup()
        if self._conversation_task:
            await self.cancel_task(self._conversation_task)
            self._conversation_task = None
        if self._ivr_task:
            await self.cancel_task(self._ivr_task)
            self._ivr_task = None
        if self._ivr_completed_task:
            await self.cancel_task(self._ivr_completed_task)
            self._ivr_completed_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self._gate_open:
            await self.push_frame(frame, direction)
        elif isinstance(frame, (SystemFrame, EndFrame, StopFrame)):
            await self.push_frame(frame, direction)

    async def _wait_for_conversation(self):
        await self._conversation_notifier.wait()
        self._gate_open = True
        logger.trace("[Triage] Gate opened (conversation)")

    async def _wait_for_ivr(self):
        await self._ivr_notifier.wait()
        self._gate_open = True
        logger.trace("[Triage] Gate opened (IVR)")

    async def _wait_for_ivr_completed(self):
        await self._ivr_completed_notifier.wait()
        self._gate_open = True
        logger.trace("[Triage] Gate opened (IVR completed)")


class ClassifierGate(FrameProcessor):
    """Stops classification after decision is made.

    Starts open. Closes when gate_notifier signals (any decision made).
    When closed, only system frames pass through.
    Speaking frames only pass if conversation was NOT detected (needed for voicemail timing).
    """

    def __init__(self, gate_notifier: BaseNotifier, conversation_notifier: BaseNotifier):
        super().__init__()
        self._gate_notifier = gate_notifier
        self._conversation_notifier = conversation_notifier
        self._gate_open = True
        self._conversation_detected = False
        self._gate_task: Optional[asyncio.Task] = None
        self._conversation_task: Optional[asyncio.Task] = None

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        self._gate_task = self.create_task(self._wait_for_decision())
        self._conversation_task = self.create_task(self._wait_for_conversation())

    async def cleanup(self):
        await super().cleanup()
        if self._gate_task:
            await self.cancel_task(self._gate_task)
            self._gate_task = None
        if self._conversation_task:
            await self.cancel_task(self._conversation_task)
            self._conversation_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self._gate_open:
            await self.push_frame(frame, direction)
        elif isinstance(frame, (UserStartedSpeakingFrame, UserStoppedSpeakingFrame)):
            if not self._conversation_detected:
                await self.push_frame(frame, direction)
        elif isinstance(frame, (SystemFrame, EndFrame, StopFrame)):
            await self.push_frame(frame, direction)

    async def _wait_for_decision(self):
        await self._gate_notifier.wait()
        self._gate_open = False
        logger.trace("[Triage] ClassifierGate closed")

    async def _wait_for_conversation(self):
        await self._conversation_notifier.wait()
        self._conversation_detected = True
        logger.trace("[Triage] Conversation detected")


class TriageProcessor(FrameProcessor):
    """Processes classifier LLM output and emits triage events.

    Aggregates LLM tokens, searches for CONVERSATION/IVR/VOICEMAIL,
    notifies appropriate notifier, and emits event with conversation history.
    """

    def __init__(
        self,
        *,
        gate_notifier: BaseNotifier,
        conversation_notifier: BaseNotifier,
        ivr_notifier: BaseNotifier,
        voicemail_notifier: BaseNotifier,
        voicemail_response_delay: float,
        context,
    ):
        super().__init__()
        self._gate_notifier = gate_notifier
        self._conversation_notifier = conversation_notifier
        self._ivr_notifier = ivr_notifier
        self._voicemail_notifier = voicemail_notifier
        self._voicemail_response_delay = voicemail_response_delay
        self._context = context

        self._register_event_handler(TriageEvent.CONVERSATION_DETECTED)
        self._register_event_handler(TriageEvent.IVR_DETECTED)
        self._register_event_handler(TriageEvent.VOICEMAIL_DETECTED)

        self._processing_response = False
        self._response_buffer = ""
        self._decision_made = False

        self._voicemail_detected = False
        self._voicemail_task: Optional[asyncio.Task] = None
        self._voicemail_event = asyncio.Event()
        self._voicemail_event.set()

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        self._voicemail_task = self.create_task(self._delayed_voicemail_handler())

    async def cleanup(self):
        await super().cleanup()
        if self._voicemail_task:
            await self.cancel_task(self._voicemail_task)
            self._voicemail_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._processing_response = True
            self._response_buffer = ""

        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._processing_response and not self._decision_made:
                await self._process_classification(self._response_buffer.strip())
            self._processing_response = False
            self._response_buffer = ""

        elif isinstance(frame, LLMTextFrame) and self._processing_response:
            self._response_buffer += frame.text

        elif isinstance(frame, UserStartedSpeakingFrame):
            if self._voicemail_detected:
                self._voicemail_event.set()

        elif isinstance(frame, UserStoppedSpeakingFrame):
            if self._voicemail_detected:
                self._voicemail_event.clear()

        else:
            await self.push_frame(frame, direction)

    def _get_conversation_history(self) -> list:
        """Extract messages from classifier context, excluding system prompt."""
        try:
            messages = self._context.get_messages()
            return list(messages[1:]) if len(messages) > 1 else []
        except Exception as e:
            logger.warning(f"Failed to get conversation history: {e}")
            return []

    async def _process_classification(self, full_response: str):
        """Process classifier response and trigger appropriate action."""
        if self._decision_made:
            return

        response = full_response.upper()
        logger.debug(f"[Triage] Classifying: '{full_response}'")

        conversation_history = self._get_conversation_history()

        if TriageClassification.CONVERSATION in response:
            self._decision_made = True
            logger.info("[Triage] Classification: CONVERSATION")
            await self._gate_notifier.notify()
            await self._conversation_notifier.notify()
            await self._call_event_handler(TriageEvent.CONVERSATION_DETECTED, conversation_history)

        elif TriageClassification.IVR in response:
            self._decision_made = True
            logger.info("[Triage] Classification: IVR")
            await self._gate_notifier.notify()
            await self._ivr_notifier.notify()
            await self._call_event_handler(TriageEvent.IVR_DETECTED, conversation_history)

        elif TriageClassification.VOICEMAIL in response:
            self._decision_made = True
            self._voicemail_detected = True
            logger.info("[Triage] Classification: VOICEMAIL")
            await self._gate_notifier.notify()
            await self._voicemail_notifier.notify()
            await self.push_interruption_task_frame_and_wait()
            self._voicemail_event.clear()

        else:
            logger.debug(f"[Triage] No classification in: '{full_response}'")

    async def _delayed_voicemail_handler(self):
        """Wait for voicemail delay, then emit event."""
        while True:
            try:
                await asyncio.wait_for(
                    self._voicemail_event.wait(),
                    timeout=self._voicemail_response_delay
                )
                await asyncio.sleep(0.1)
            except asyncio.TimeoutError:
                await self._call_event_handler(TriageEvent.VOICEMAIL_DETECTED)
                break


class TTSGate(FrameProcessor):
    """Buffers TTS frames until classification decision.

    - CONVERSATION: Release all buffered frames
    - IVR: Clear buffer (navigate silently)
    - VOICEMAIL: Clear buffer (will play VM message instead)
    """

    def __init__(
        self,
        conversation_notifier: BaseNotifier,
        ivr_notifier: BaseNotifier,
        voicemail_notifier: BaseNotifier,
    ):
        super().__init__()
        self._conversation_notifier = conversation_notifier
        self._ivr_notifier = ivr_notifier
        self._voicemail_notifier = voicemail_notifier
        self._frame_buffer: List[tuple[Frame, FrameDirection]] = []
        self._gating_active = True
        self._conversation_task: Optional[asyncio.Task] = None
        self._ivr_task: Optional[asyncio.Task] = None
        self._voicemail_task: Optional[asyncio.Task] = None

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        self._conversation_task = self.create_task(self._wait_for_conversation())
        self._ivr_task = self.create_task(self._wait_for_ivr())
        self._voicemail_task = self.create_task(self._wait_for_voicemail())

    async def cleanup(self):
        await super().cleanup()
        for task in [self._conversation_task, self._ivr_task, self._voicemail_task]:
            if task:
                await self.cancel_task(task)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self._gating_active and isinstance(
            frame, (TTSStartedFrame, TTSStoppedFrame, TTSTextFrame, TTSAudioRawFrame)
        ):
            self._frame_buffer.append((frame, direction))
        else:
            await self.push_frame(frame, direction)

    async def _wait_for_conversation(self):
        await self._conversation_notifier.wait()
        self._gating_active = False
        for frame, direction in self._frame_buffer:
            await self.push_frame(frame, direction)
        self._frame_buffer.clear()
        logger.trace("[Triage] TTSGate released buffered frames")

    async def _wait_for_ivr(self):
        await self._ivr_notifier.wait()
        self._gating_active = False
        self._frame_buffer.clear()
        logger.trace("[Triage] TTSGate cleared buffer (IVR)")

    async def _wait_for_voicemail(self):
        await self._voicemail_notifier.wait()
        self._gating_active = False
        self._frame_buffer.clear()
        logger.trace("[Triage] TTSGate cleared buffer (voicemail)")
