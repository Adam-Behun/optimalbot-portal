"""LLM Context Observer - logs LLM context in readable multi-line format."""

import re
from typing import List

from loguru import logger
from pipecat.frames.frames import FunctionCallInProgressFrame, LLMContextFrame, LLMSetToolsFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContextFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService


class LLMContextObserver(BaseObserver):
    """Logs LLM context in readable multi-line format."""

    def __init__(self):
        super().__init__()

    def _format_messages(self, messages: List[dict]) -> List[str]:
        lines = []
        for msg in messages:
            role = msg.get('role', '?')
            content = msg.get('content', '')
            if content:
                # Collapse whitespace but keep full content
                content = re.sub(r'\s+', ' ', content).strip()
                lines.append(f"\n  [{role}] {content}")
        return lines

    async def on_push_frame(self, data: FramePushed):
        dst = data.destination
        frame = data.frame

        if not isinstance(dst, LLMService):
            return

        if isinstance(frame, (LLMContextFrame, OpenAILLMContextFrame)):
            messages = (
                frame.context.messages
                if isinstance(frame, OpenAILLMContextFrame)
                else frame.context.get_messages()
            )

            lines = [f"[LLM] {dst} context ({len(messages)} messages):"]
            lines.extend(self._format_messages(messages))
            logger.debug('\n'.join(lines))

        elif (
            isinstance(frame, FunctionCallInProgressFrame)
            and data.direction != FrameDirection.DOWNSTREAM
        ):
            logger.debug(f"[LLM] Function call: {frame.function_name}({frame.arguments})")
