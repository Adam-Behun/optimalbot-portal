"""STT transcript logger for DEBUG level visibility."""

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TranscriptLogger(FrameProcessor):
    """Logs STT transcriptions at DEBUG level."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text:
            logger.debug(f'[STT] User: "{frame.text}"')
        await self.push_frame(frame, direction)
