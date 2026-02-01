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
from pipecat.utils.text.pattern_pair_aggregator import MatchAction, PatternPairAggregator

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
4. If unsure, respond with <ivr>wait</ivr> - waiting is always safe

WAIT - Respond with <ivr>wait</ivr> when:
- NONE of the presented options are relevant to your navigation goal
- The transcription appears to be cut off mid-sentence
- You see partial menu options (e.g., "for claims status" without "press X")
- The transcription seems incomplete or you suspect more options are coming
- You just pressed a button and hear a menu fragment (system is still announcing)
- The text doesn't start with a greeting or clear menu structure

IMPORTANT: When in doubt, WAIT. IVR menus often arrive in fragments. A single
menu item like "for claims status" usually means more options are coming.

COMPLETION - Respond with <ivr>completed</ivr> when:
- A HUMAN answers: "Hello, this is [Name]", "How can I help you?"
- Transfer confirms: "Transferring you now", "Connecting you to [Name/Department]"
- You reach the department: "You've reached [target department]"
- DO NOT mark completed for queue updates like "You are next", "shortly", "please hold"

STUCK - Respond with <ivr>stuck</ivr> ONLY when:
- Same menu repeated 3+ times (loop detected)
- You encounter "Invalid selection" or "Please try again" after valid input
- The system explicitly says there's no path forward (fax-only, website-only)
- You've waited through multiple complete menus with NO relevant options

NEVER mark stuck after receiving just one partial fragment. If you're unsure
whether you're stuck or just waiting for more menu options, choose WAIT.

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
        self._aggregator.add_pattern("dtmf", "<dtmf>", "</dtmf>", action=MatchAction.REMOVE)
        self._aggregator.on_pattern_match("dtmf", self._handle_dtmf_action)

        self._aggregator.add_pattern("ivr", "<ivr>", "</ivr>", action=MatchAction.REMOVE)
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

        # Push context update UPSTREAM to OpenAI (processor is now after LLM)
        await self.push_frame(
            LLMMessagesUpdateFrame(messages=messages, run_llm=True),
            FrameDirection.UPSTREAM
        )

        # VAD params go UPSTREAM to the input/STT side
        await self.push_frame(
            VADParamsUpdateFrame(params=self._ivr_vad_params),
            FrameDirection.UPSTREAM
        )

        logger.info("[IVR] Activated")

    def deactivate(self):
        """Deactivate IVR navigation mode."""
        self._active = False
        logger.info("[IVR] Completed → human")

    def is_active(self) -> bool:
        """Check if IVR navigation is active."""
        return self._active

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._active:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            # aggregate() is an async iterator that yields PatternMatch objects
            async for result in self._aggregator.aggregate(frame.text):
                # result.text contains the non-pattern text to pass through
                await self.push_frame(LLMTextFrame(result.text), direction)

        elif isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            # Flush any remaining text from the aggregator
            remaining = self._aggregator.text  # Returns Aggregation object
            if remaining and remaining.text:
                await self.push_frame(LLMTextFrame(remaining.text), direction)
            await self._aggregator.reset()
            await self.push_frame(frame, direction)

        else:
            await self.push_frame(frame, direction)

    async def _handle_dtmf_action(self, match):
        """Handle DTMF pattern - send keypad tone."""
        value = match.text
        logger.debug(f"[IVR] DTMF: {value}")

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
        status = match.text.lower()
        logger.debug(f"[IVR] Status: {status}")

        if status == IVRStatus.COMPLETED:
            self.deactivate()
            await self._call_event_handler(IVREvent.STATUS_CHANGED, IVRStatus.COMPLETED)

        elif status == IVRStatus.STUCK:
            self.deactivate()
            await self._call_event_handler(IVREvent.STATUS_CHANGED, IVRStatus.STUCK)

        elif status == IVRStatus.WAIT:
            logger.trace("[IVR] Waiting")

        text_frame = TextFrame(text=f"<ivr>{status}</ivr>")
        text_frame.skip_tts = True
        await self.push_frame(text_frame)
