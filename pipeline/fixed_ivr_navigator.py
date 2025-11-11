"""Fixed IVRNavigator that prevents StartFrame race condition.

This module provides a fixed version of Pipecat's IVRNavigator that prevents
the race condition where OutputTransportReadyFrame arrives before StartFrame
completes propagation.

The bug exists in Pipecat v0.0.93 where IVRProcessor.process_frame() pushes
LLMMessagesUpdateFrame(messages=messages) without run_llm=False during StartFrame
processing, causing the LLM to execute immediately before the pipeline is ready.

This fix is forward-compatible: if Pipecat fixes the bug upstream, this code
will continue to work correctly since run_llm=False is the proper behavior for
initial context setup without user input.
"""

import logging
from typing import Optional

from pipecat.extensions.ivr.ivr_navigator import IVRNavigator, IVRProcessor
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    LLMMessagesUpdateFrame,
    LLMTextFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class FixedIVRProcessor(IVRProcessor):
    """IVRProcessor with StartFrame race condition fix.

    Sets run_llm=False when pushing initial classifier prompt during StartFrame
    processing to prevent LLM execution before pipeline initialization completes.

    This is the proper behavior since:
    - StartFrame should complete propagation before any LLM processing
    - The classifier prompt is just initial context, not a user query
    - The LLM will run naturally on the first real STT transcript frame
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames with fixed StartFrame handling."""
        await super(IVRProcessor, self).process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            # Push the StartFrame right away
            await self.push_frame(frame, direction)

            # Set the classifier prompt WITHOUT triggering immediate LLM run
            # This is the critical fix: run_llm=False prevents the race condition
            messages = [{"role": "system", "content": self._classifier_prompt}]
            llm_update_frame = LLMMessagesUpdateFrame(messages=messages, run_llm=False)
            await self.push_frame(llm_update_frame, FrameDirection.UPSTREAM)

        elif isinstance(frame, LLMTextFrame):
            # Process text through the pattern aggregator
            result = await self._aggregator.aggregate(frame.text)
            if result:
                # Push aggregated text that doesn't contain XML patterns
                await self.push_frame(LLMTextFrame(result), direction)

        else:
            await self.push_frame(frame, direction)


class FixedIVRNavigator(IVRNavigator):
    """IVRNavigator with StartFrame race condition fix.

    Uses FixedIVRProcessor to prevent immediate LLM execution during StartFrame
    propagation, eliminating the race condition where output frames arrive at
    the sink before StartFrame completes.

    Usage:
        Replace IVRNavigator with FixedIVRNavigator in your pipeline:

        from pipeline.fixed_ivr_navigator import FixedIVRNavigator

        ivr_navigator = FixedIVRNavigator(
            llm=llm_service,
            ivr_prompt="Your navigation goal",
            ivr_vad_params=VADParams(stop_secs=2.0)
        )

    Forward Compatibility:
        If Pipecat fixes the bug upstream by adding run_llm=False, this class
        will continue to work correctly. You can then remove this custom class
        and use the standard IVRNavigator once confirmed fixed.
    """

    def __init__(
        self,
        *,
        llm: LLMService,
        ivr_prompt: str,
        ivr_vad_params: Optional[VADParams] = None,
    ):
        """Initialize with fixed IVR processor.

        Args:
            llm: LLM service for text generation and decision making.
            ivr_prompt: Navigation goal prompt integrated with IVR navigation instructions.
            ivr_vad_params: VAD parameters for IVR navigation mode. If None, defaults to VADParams(stop_secs=2.0).
        """
        # Store parameters needed for FixedIVRProcessor
        self._llm = llm
        self._ivr_prompt = self.IVR_NAVIGATION_BASE.format(goal=ivr_prompt)
        self._ivr_vad_params = ivr_vad_params or VADParams(stop_secs=2.0)
        self._classifier_prompt = self.CLASSIFIER_PROMPT

        # Create fixed processor instead of default IVRProcessor
        self._ivr_processor = FixedIVRProcessor(
            classifier_prompt=self._classifier_prompt,
            ivr_prompt=self._ivr_prompt,
            ivr_vad_params=self._ivr_vad_params,
        )

        # Initialize pipeline with LLM and fixed processor
        # Call Pipeline.__init__ directly to avoid IVRNavigator's default processor creation
        from pipecat.pipeline.pipeline import Pipeline
        Pipeline.__init__(self, [self._llm, self._ivr_processor])

        # Register IVR events
        self._register_event_handler("on_conversation_detected")
        self._register_event_handler("on_ivr_status_changed")

        logger.debug("FixedIVRNavigator initialized with StartFrame race condition fix")
