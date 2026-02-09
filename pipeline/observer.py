"""Silent observer pipeline branch for background data extraction.

The observer LLM listens to both sides of the conversation (via
TranscriptionFrame for user speech and TTSTextFrame for bot speech
injected by ConsumerProcessor) and extracts structured data each turn.
It never produces audible output — NullFilter at the branch terminus
blocks everything except system frames.
"""

import json
from collections import deque

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.filters.frame_filter import FrameFilter
from pipecat.processors.filters.null_filter import NullFilter
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class ObserverContextManager(FrameProcessor):
    """Builds rolling-window LLM context from both sides of a conversation.

    Accumulates bot speech (TTSTextFrame from ConsumerProcessor) and user
    speech (TranscriptionFrame) into a deque of recent turns.  On each
    UserStoppedSpeakingFrame, constructs a fresh LLMContext with:
      - system prompt  (role description + normalization rules + current state JSON)
      - recent turns   (assistant / user message pairs)
    and pushes an LLMContextFrame downstream to the observer LLM.
    """

    def __init__(
        self,
        system_prompt: str,
        tools,
        tool_choice: str,
        flow_ref,
        extraction_fields: list[str] | None = None,
        window_size: int = 10,
    ):
        super().__init__()
        self._system_prompt = system_prompt
        self._tools = tools
        self._tool_choice = tool_choice
        self._flow_ref = flow_ref  # strong ref to flow; only reads flow_manager.state
        self._extraction_fields = extraction_fields or []
        self._recent_turns: deque = deque(maxlen=window_size)
        self._current_turn_text = ""
        self._current_assistant_text = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSTextFrame):
            # Bot speech injected by ConsumerProcessor
            self._current_assistant_text += frame.text
            # Don't push downstream — observer doesn't need raw TTS frames
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            # Flush any pending assistant text as an assistant turn
            if self._current_assistant_text.strip():
                self._recent_turns.append(
                    {"role": "assistant", "content": self._current_assistant_text.strip()}
                )
                self._current_assistant_text = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            # Consume for context building — observer LLM only needs the built-up LLMContextFrame
            self._current_turn_text += frame.text + " "
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            # Flush user text as a user turn
            if self._current_turn_text.strip():
                self._recent_turns.append(
                    {"role": "user", "content": self._current_turn_text.strip()}
                )
                self._current_turn_text = ""
                await self._build_and_push_context()
            await self.push_frame(frame, direction)
            return

        # All other frames pass through
        await self.push_frame(frame, direction)

    async def _build_and_push_context(self):
        """Build fresh LLMContext with state + rolling window and push downstream."""
        # Get current state from flow_manager
        state_dict = {}
        if self._flow_ref and hasattr(self._flow_ref, "flow_manager") and self._flow_ref.flow_manager:
            fm_state = self._flow_ref.flow_manager.state
            for field in self._extraction_fields:
                val = fm_state.get(field)
                if val:
                    state_dict[field] = val

        state_json = json.dumps(state_dict, indent=2) if state_dict else "{}"

        system_content = (
            f"{self._system_prompt}\n\n"
            f"# Currently Extracted Data\n"
            f"```json\n{state_json}\n```"
        )

        messages = [{"role": "system", "content": system_content}]
        for turn in self._recent_turns:
            messages.append({"role": turn["role"], "content": turn["content"]})

        context = LLMContext(
            messages=messages,
            tools=self._tools,
            tool_choice=self._tool_choice,
        )

        logger.debug(f"[Observer] Pushing context with {len(self._recent_turns)} turns, {len(state_dict)} extracted fields")
        await self.push_frame(LLMContextFrame(context=context))


def create_observer_branch(observer_context_manager, observer_llm, bot_speech_consumer):
    """Create the observer pipeline branch processors list.

    Layout:
        FrameFilter(TranscriptionFrame) → ConsumerProcessor → ObserverContextManager → observer_llm → NullFilter

    FrameFilter blocks FlowManager frames (LLMSetToolsFrame, LLMMessagesUpdateFrame,
    LLMRunFrame) while allowing TranscriptionFrame and SystemFrames through.
    ConsumerProcessor is placed AFTER the filter so its injected TTSTextFrames
    bypass the filter.
    """
    return [
        # TranscriptionFrame is the only non-system frame needed.
        # UserStarted/StoppedSpeakingFrame are SystemFrames and pass through automatically.
        FrameFilter(types=(TranscriptionFrame,)),
        bot_speech_consumer,
        observer_context_manager,
        observer_llm,
        NullFilter(),
    ]
