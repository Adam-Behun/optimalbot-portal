from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartInterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class PassThroughProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class SafetyClassifier(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._register_event_handler("on_emergency_detected")
        self._register_event_handler("on_staff_requested")
        self._processing = False
        self._buffer = ""

    async def cleanup(self):
        await super().cleanup()
        self._processing = False
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._processing = True
            self._buffer = ""
        elif isinstance(frame, LLMTextFrame) and self._processing:
            self._buffer += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._processing:
                await self._classify(self._buffer.strip().upper())
            self._processing = False
            self._buffer = ""
        else:
            await self.push_frame(frame, direction)

    async def _classify(self, response: str):
        if response == "EMERGENCY":
            logger.warning("SafetyClassifier: EMERGENCY detected")
            await self._call_event_handler("on_emergency_detected")
        elif response == "STAFF_REQUEST":
            logger.info("SafetyClassifier: STAFF_REQUEST detected")
            await self._call_event_handler("on_staff_requested")
        elif response != "OK":
            logger.debug(f"SafetyClassifier: unexpected response '{response}'")
