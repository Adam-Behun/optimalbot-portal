"""Fixed IVRNavigator that prevents StartFrame race condition and LLM switching timing issues.

This module provides a fixed version of Pipecat's IVRNavigator that fixes two issues:

1. StartFrame race condition: Prevents OutputTransportReadyFrame arriving before StartFrame
   completes propagation by setting run_llm=False during initial context setup.

2. LLM switching timing: Emits "on_ivr_pre_detected" event BEFORE triggering LLM execution
   when IVR is detected, allowing handlers to switch from classifier_llm to main_llm before
   the first IVR navigation inference runs.

The LLM switching fix addresses the race condition where:
- IVRProcessor detects IVR mode and immediately pushes LLMMessagesUpdateFrame(run_llm=True)
- This triggers LLM execution with classifier_llm (still active)
- THEN the on_ivr_status_changed handler fires and switches to main_llm (too late)
- Result: First IVR question processed by wrong LLM, subsequent questions work fine

The fix adds a pre-event hook with async delay to allow the switch frame to propagate
through the pipeline before the LLM is triggered.
"""

import asyncio
import logging
from typing import Optional

from pipecat.extensions.ivr.ivr_navigator import IVRNavigator, IVRProcessor, IVRStatus
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
    """IVRProcessor with StartFrame race condition fix and LLM switching timing fix.

    Fixes two issues:
    1. Sets run_llm=False when pushing initial classifier prompt during StartFrame
       processing to prevent LLM execution before pipeline initialization completes.

    2. Emits "on_ivr_pre_detected" event BEFORE triggering LLM execution when IVR
       is detected, allowing external handlers to switch LLMs before inference runs.

    This is the proper behavior since:
    - StartFrame should complete propagation before any LLM processing
    - The classifier prompt is just initial context, not a user query
    - The LLM will run naturally on the first real STT transcript frame
    - LLM switching must happen before LLMMessagesUpdateFrame(run_llm=True) is pushed
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

    async def _handle_mode_action(self, match):
        """Handle mode action with robust parsing to handle interruptions and malformed output.

        Fixes issue where user interruptions during LLM streaming cause PatternPairAggregator
        to produce malformed mode values like "conversation<mode>conversation" due to:
        1. Partial XML tags from interrupted streams
        2. Buffer state from previous processing
        3. LLM outputting extra text beyond the required tags

        This override makes mode detection resilient by:
        - Using substring matching instead of exact equality
        - Handling partial/corrupted XML tags
        - Trimming whitespace and normalizing case

        Args:
            match: The pattern match containing mode content.
        """
        mode_raw = match.content
        logger.debug(f"Mode detected (raw): {mode_raw}")

        # Clean the mode string - handle cases like "conversation<mode>conversation"
        mode = mode_raw.strip().lower()

        # Check if mode contains "conversation" or "ivr" (robust to interruptions)
        if "conversation" in mode:
            mode_clean = "conversation"
        elif "ivr" in mode:
            mode_clean = "ivr"
        else:
            logger.warning(f"Unknown mode detected: {mode_raw}")
            return

        logger.debug(f"Mode detected (cleaned): {mode_clean}")

        # Call the appropriate handler
        if mode_clean == "conversation":
            await self._handle_conversation()
        elif mode_clean == "ivr":
            await self._handle_ivr_detected()

    async def _handle_conversation(self):
        """Handle conversation mode by switching to conversation mode.

        Emit an on_conversation_detected event with saved conversation history.
        """
        logger.debug("Conversation detected - emitting on_conversation_detected event")

        # Extract conversation history for the event handler
        conversation_history = self._get_conversation_history()

        await self._call_event_handler("on_conversation_detected", conversation_history)

    async def _handle_ivr_detected(self):
        """Handle IVR detection with pre-event for LLM switching.

        Emits "on_ivr_pre_detected" event BEFORE triggering LLM execution,
        allowing external handlers to switch from classifier_llm to main_llm
        before the first IVR navigation inference runs.

        The async delay ensures the ManuallySwitchServiceFrame has time to
        propagate through the pipeline before LLMMessagesUpdateFrame(run_llm=True)
        triggers LLM execution.
        """
        logger.debug("IVR detected - emitting pre-detection event for LLM switch")

        # CRITICAL: Emit pre-event BEFORE triggering LLM
        # This allows handlers to push ManuallySwitchServiceFrame upstream
        await self._call_event_handler("on_ivr_pre_detected")

        # Small delay to allow switch frame to propagate through the pipeline
        # This ensures the LLMSwitcher's active service is updated before inference
        await asyncio.sleep(0.05)

        # NOW create the IVR navigation context and trigger LLM
        # The LLM that runs will be the one set by the pre-event handler
        messages = [{"role": "system", "content": self._ivr_prompt}]
        conversation_history = self._get_conversation_history()
        if conversation_history:
            messages.extend(conversation_history)

        # Push the messages upstream and run the LLM with the new context
        llm_update_frame = LLMMessagesUpdateFrame(messages=messages, run_llm=True)
        await self.push_frame(llm_update_frame, FrameDirection.UPSTREAM)

        # Emit standard status changed event (for logging/tracking)
        await self._call_event_handler("on_ivr_status_changed", IVRStatus.DETECTED)


class FixedIVRNavigator(IVRNavigator):
    """IVRNavigator with StartFrame race condition fix and LLM switching timing fix.

    Uses FixedIVRProcessor to fix two issues:
    1. Prevents immediate LLM execution during StartFrame propagation
    2. Emits "on_ivr_pre_detected" event before LLM execution for proper LLM switching

    The LLM switching fix ensures that when transitioning from classifier mode to
    IVR navigation mode, the system switches to main_llm BEFORE the first IVR
    question is processed, preventing the first navigation inference from running
    on the wrong LLM.

    Usage:
        Replace IVRNavigator with FixedIVRNavigator in your pipeline:

        from pipeline.fixed_ivr_navigator import FixedIVRNavigator

        ivr_navigator = FixedIVRNavigator(
            llm=llm_service,
            ivr_prompt="Your navigation goal",
            ivr_vad_params=VADParams(stop_secs=2.0)
        )

        # In your event handlers:
        @ivr_navigator.event_handler("on_ivr_pre_detected")
        async def on_ivr_pre_detected(processor):
            # Switch to main_llm BEFORE IVR navigation starts
            await pipeline.context_aggregator.assistant().push_frame(
                ManuallySwitchServiceFrame(service=main_llm),
                FrameDirection.UPSTREAM
            )

    Events:
        - on_ivr_pre_detected: Fires BEFORE LLM execution when IVR is detected
        - on_ivr_status_changed: Fires AFTER LLM starts (standard Pipecat event)
        - on_conversation_detected: Fires when human conversation is detected

    Forward Compatibility:
        If Pipecat fixes these bugs upstream, this class will continue to work
        correctly. You can then remove this custom class once confirmed fixed.
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

        # Register IVR events (including new pre-detection event)
        self._register_event_handler("on_ivr_pre_detected")
        self._register_event_handler("on_conversation_detected")
        self._register_event_handler("on_ivr_status_changed")

        logger.debug("FixedIVRNavigator initialized with StartFrame and LLM switching fixes")
