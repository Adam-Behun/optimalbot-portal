"""IVR Human Detector - detects when a human answers during IVR navigation."""

import asyncio
from typing import Optional

from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pipeline.triage_processors import TriageClassification


CLASSIFIER_PROMPT = """Classify this phone call transcription as IVR or human.

IVR (automated system) examples:
- "Press 1 for...", "For X, press Y"
- "Please hold", "Thank you for holding", "Thank you for your patience"
- "Your estimated wait time is...", "You are next in queue"
- "A representative will be with you shortly"
- "Transferring your call", "Please wait while we connect you"

CONVERSATION (human) indicators:
- Person introduces themselves: "This is [Name]", "My name is [Name]", "[Name] speaking"
- Asks how to help: "How can I help you?", "How may I assist you?"
- Mentions their department: "This is [Name] with [department]"

CRITICAL: Generic hold messages like "Thank you for your patience" or "A representative will be with you shortly" are IVR, NOT human. Humans identify themselves by name.

Output EXACTLY one word: CONVERSATION or IVR"""


class IVRHumanDetector(FrameProcessor):
    """Detects when a human answers during IVR navigation.

    Uses direct Groq API calls to classify transcriptions.
    Emits on_human_detected event when human speech is detected.

    Uses debouncing to handle fragmented transcriptions: waits for 300ms
    of silence after detecting human speech before triggering completion.
    Any new transcription resets the timer.
    """

    CLASSIFICATION_TIMEOUT = 3.0
    DEBOUNCE_DELAY = 0.3  # 300ms silence before triggering

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        super().__init__()
        self._model = model
        self._active = False
        self._human_detected = False
        self._trigger_task: Optional[asyncio.Task] = None
        self._accumulated_text = ""
        self._register_event_handler("on_human_detected")

        try:
            from groq import AsyncGroq
            self._client = AsyncGroq(api_key=api_key)
        except ImportError:
            logger.warning("[IVRHumanDetector] groq package not installed")
            self._client = None

    def activate(self) -> None:
        """Start monitoring transcriptions for human speech."""
        self._active = True
        self._human_detected = False
        self._accumulated_text = ""
        logger.info("[IVRHumanDetector] Activated")

    def deactivate(self) -> None:
        """Stop monitoring."""
        self._active = False
        self._human_detected = False
        if self._trigger_task and not self._trigger_task.done():
            self._trigger_task.cancel()
        self._trigger_task = None
        logger.info("[IVRHumanDetector] Deactivated")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if self._active and self._client and isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                # If human already detected, reset debounce timer on any new transcription
                if self._human_detected:
                    self._accumulated_text += " " + text
                    self._reset_trigger_timer()
                else:
                    self.create_task(self._classify(text))

    def _reset_trigger_timer(self):
        """Reset the debounce timer - called when new transcription arrives."""
        if self._trigger_task and not self._trigger_task.done():
            self._trigger_task.cancel()
        self._trigger_task = self.create_task(self._delayed_trigger())
        logger.debug(f"[IVRHumanDetector] Debounce reset, accumulated: '{self._accumulated_text[:50]}...'")

    async def _delayed_trigger(self):
        """Wait for debounce delay then trigger completion."""
        try:
            await asyncio.sleep(self.DEBOUNCE_DELAY)
            if self._active and self._human_detected:
                logger.info(f"[IVRHumanDetector] Human confirmed: '{self._accumulated_text[:60]}'")
                self.deactivate()
                await self._call_event_handler("on_human_detected", self._accumulated_text.strip())
        except asyncio.CancelledError:
            pass  # Timer was reset by new transcription

    async def _classify(self, text: str):
        """Classify transcription and start debounce timer if human detected."""
        if not self._active or not self._client:
            return

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": CLASSIFIER_PROMPT},
                        {"role": "user", "content": text}
                    ],
                    max_tokens=10,
                    temperature=0
                ),
                timeout=self.CLASSIFICATION_TIMEOUT
            )
            result = response.choices[0].message.content.strip().upper()
            logger.debug(f"[IVRHumanDetector] '{text[:40]}' → {result}")

            if result == TriageClassification.CONVERSATION:
                logger.info(f"[IVRHumanDetector] Human detected, starting debounce: '{text[:50]}'")
                self._human_detected = True
                self._accumulated_text = text
                self._reset_trigger_timer()

        except asyncio.TimeoutError:
            logger.warning("[IVRHumanDetector] Classification timeout")
        except Exception as e:
            logger.warning(f"[IVRHumanDetector] Classification failed: {e}")
