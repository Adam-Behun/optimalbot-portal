"""IVR Navigation Processor - handles DTMF menu navigation without classification."""

from typing import Optional
from loguru import logger

from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMMessagesUpdateFrame,
    LLMTextFrame,
    OutputDTMFUrgentFrame,
    TextFrame,
    VADParamsUpdateFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.text.pattern_pair_aggregator import PatternPairAggregator


# =============================================================================
# CONSTANTS - Used by evals to ensure sync with production
# =============================================================================

class IVRStatus:
    """IVR navigation status values."""
    DETECTED = "detected"
    COMPLETED = "completed"
    STUCK = "stuck"
    WAIT = "wait"


class IVREvent:
    """Events fired by IVR navigation processor."""
    DTMF_PRESSED = "on_dtmf_pressed"
    STATUS_CHANGED = "on_ivr_status_changed"


# Regex patterns for parsing LLM output - evals should import these
DTMF_PATTERN = r'<dtmf>(\d|\*|#)</dtmf>'
IVR_STATUS_PATTERN = r'<ivr>(completed|stuck|wait)</ivr>'


class IVRNavigationProcessor(FrameProcessor):
    """Processes IVR menu navigation using DTMF tones.

    This processor is INACTIVE until activate() is called.
    When active, it:
    - Parses XML patterns: <dtmf>1</dtmf>, <ivr>completed</ivr>
    - Sends DTMF frames for keypad entries
    - Emits on_ivr_status_changed and on_dtmf_pressed events

    Does NOT handle classification - that's TriageDetector's job.
    """

    IVR_NAVIGATION_PROMPT = """You are navigating an Interactive Voice Response (IVR) system.

YOUR NAVIGATION GOAL:
{goal}

CRITICAL - PROVIDER VS MEMBER OPTIONS:
You are calling AS A HEALTHCARE PROVIDER to verify a patient's benefits.
- "Member eligibility" / "Member services" = for patients checking their OWN benefits (WRONG)
- "Provider services" / "Healthcare professionals" = for providers verifying patients (CORRECT)
- If no provider option exists, choose "operator", "representative", or "0" to reach a human
- NEVER choose member-facing options - they won't help with provider inquiries

HOLD VS CALLBACK DECISIONS:
- PREFER HOLDING over callbacks - callbacks are unreliable and introduce delays
- Hold for waits under 30 minutes - staying on the line is more predictable
- Only request callback for very long waits (45+ minutes)
- If callback ETA is longer than hold time, CANCEL callback and hold instead

NAVIGATION RULES:
1. For menu options ("Press 1 for..."), respond: <dtmf>NUMBER</dtmf>
2. For sequences, enter digits separately: <dtmf>1</dtmf><dtmf>2</dtmf>
3. For verbal responses, respond with natural language text
4. If NO options are relevant, respond with <ivr>wait</ivr>
5. If transcription is incomplete, respond with <ivr>wait</ivr>

COMPLETION - Respond with <ivr>completed</ivr> when:
- A HUMAN answers: "Hello, this is [Name]", "How can I help you?"
- Transfer confirms: "Transferring you now", "Connecting you to [Name/Department]"
- You reach the department: "You've reached [target department]"
- DO NOT mark completed for queue updates like "You are next", "shortly", "please hold"

STUCK - Respond with <ivr>stuck</ivr> when:
- Same menu repeated 3+ times (loop detected)
- Wrong department with NO relevant options AND no way back (e.g., pharmacy-only menu, dental-only menu)
- "Invalid selection", "Please try again" after valid input
- Dead end: fax-only, website-only, or system error

Respond: <dtmf>N</dtmf>, <ivr>completed</ivr>, <ivr>stuck</ivr>, <ivr>wait</ivr>, or text."""

    def __init__(self, *, ivr_vad_params: Optional[VADParams] = None):
        super().__init__()
        # 2.0s longer pause for IVR menus which have longer prompts
        self._ivr_vad_params = ivr_vad_params or VADParams(stop_secs=2.0)
        self._active = False
        self._ivr_prompt = ""

        self._aggregator = PatternPairAggregator()
        self._setup_xml_patterns()

        self._register_event_handler(IVREvent.STATUS_CHANGED)
        self._register_event_handler(IVREvent.DTMF_PRESSED)

    def _setup_xml_patterns(self):
        """Register DTMF and IVR status patterns."""
        self._aggregator.add_pattern_pair("dtmf", "<dtmf>", "</dtmf>", remove_match=True)
        self._aggregator.on_pattern_match("dtmf", self._handle_dtmf_action)

        self._aggregator.add_pattern_pair("ivr", "<ivr>", "</ivr>", remove_match=True)
        self._aggregator.on_pattern_match("ivr", self._handle_ivr_action)

    async def activate(self, ivr_goal: str, conversation_history: list):
        """Activate IVR navigation mode.

        Args:
            ivr_goal: Navigation goal to insert into prompt
            conversation_history: Previous conversation (IVR menu heard so far)
        """
        self._active = True
        self._ivr_prompt = self.IVR_NAVIGATION_PROMPT.format(goal=ivr_goal)

        messages = [{"role": "system", "content": self._ivr_prompt}]
        if conversation_history:
            messages.extend(conversation_history)

        await self.push_frame(
            LLMMessagesUpdateFrame(messages=messages, run_llm=True),
            FrameDirection.UPSTREAM
        )

        await self.push_frame(
            VADParamsUpdateFrame(params=self._ivr_vad_params),
            FrameDirection.UPSTREAM
        )

        logger.info("IVRNavigationProcessor: activated")

    def deactivate(self):
        """Deactivate IVR navigation mode."""
        self._active = False
        logger.info("IVRNavigationProcessor: deactivated")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._active:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            result = await self._aggregator.aggregate(frame.text)
            if result:
                await self.push_frame(LLMTextFrame(result), direction)

        elif isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            remaining = self._aggregator.text
            if remaining:
                await self.push_frame(LLMTextFrame(remaining), direction)
            self._aggregator.reset()
            await self.push_frame(frame, direction)

        else:
            await self.push_frame(frame, direction)

    async def _handle_dtmf_action(self, match):
        """Handle DTMF pattern - send keypad tone."""
        value = match.content
        logger.debug(f"IVR DTMF: {value}")

        try:
            keypad_entry = KeypadEntry(value)
            await self.push_frame(OutputDTMFUrgentFrame(button=keypad_entry))

            text_frame = TextFrame(text=f"<dtmf>{value}</dtmf>")
            text_frame.skip_tts = True
            await self.push_frame(text_frame)

            await self._call_event_handler(IVREvent.DTMF_PRESSED, value)
        except ValueError:
            logger.warning(f"Invalid DTMF value: {value}")

    async def _handle_ivr_action(self, match):
        """Handle IVR status pattern."""
        status = match.content.lower()
        logger.debug(f"IVR status: {status}")

        if status == IVRStatus.COMPLETED:
            self.deactivate()
            await self._call_event_handler(IVREvent.STATUS_CHANGED, IVRStatus.COMPLETED)

        elif status == IVRStatus.STUCK:
            self.deactivate()
            await self._call_event_handler(IVREvent.STATUS_CHANGED, IVRStatus.STUCK)

        elif status == IVRStatus.WAIT:
            logger.debug("IVR waiting for more input")

        text_frame = TextFrame(text=f"<ivr>{status}</ivr>")
        text_frame.skip_tts = True
        await self.push_frame(text_frame)
