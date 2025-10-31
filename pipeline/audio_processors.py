from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import AudioRawFrame, Frame, TextFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.audio.utils import create_stream_resampler
import audioop
import re
import logging

logger = logging.getLogger(__name__)

class AudioResampler(FrameProcessor):
    def __init__(self, target_sample_rate: int = 16000, target_channels: int = 1, **kwargs):
        super().__init__(**kwargs)
        self._resampler = create_stream_resampler()
        self.target_sample_rate = target_sample_rate
        self.target_channels = target_channels
        self._audio_frame_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AudioRawFrame):
            self._audio_frame_count += 1
            if self._audio_frame_count == 1:
                logger.info(f"🎤 AudioResampler: First audio frame received! "
                           f"(sr={frame.sample_rate}, ch={frame.num_channels}, "
                           f"len={len(frame.audio)} bytes)")
            elif self._audio_frame_count % 100 == 0:
                logger.debug(f"🎤 AudioResampler: {self._audio_frame_count} frames processed")

            audio = frame.audio
            sample_rate = frame.sample_rate
            channels = frame.num_channels
            sample_width = 2

            if channels > 1:
                audio = audioop.tomono(audio, sample_width, 0.5, 0.5)
                channels = 1

            if sample_rate != self.target_sample_rate:
                audio = await self._resampler.resample(audio, sample_rate, self.target_sample_rate)

            new_frame = AudioRawFrame(
                audio=audio,
                sample_rate=self.target_sample_rate,
                num_channels=channels
            )

            for attr in ['pts', 'transport_destination', 'id']:
                if hasattr(frame, attr):
                    setattr(new_frame, attr, getattr(frame, attr))

            await self.push_frame(new_frame, direction)
        else:
            await self.push_frame(frame, direction)

class DropEmptyAudio(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._audio_frame_count = 0
        self._dropped_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AudioRawFrame):
            if len(frame.audio) == 0:
                self._dropped_count += 1
                if self._dropped_count == 1:
                    logger.warning("🚫 DropEmptyAudio: Dropping empty audio frame")
                return
            else:
                self._audio_frame_count += 1
                if self._audio_frame_count == 1:
                    logger.info(f"✅ DropEmptyAudio: First valid audio frame passed through! "
                               f"({len(frame.audio)} bytes)")
        await self.push_frame(frame, direction)

class StateTagStripper(FrameProcessor):
    """Strips <next_state> tags from LLM responses before TTS"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            cleaned_text = re.sub(
                r'<next_state>\w+</next_state>',
                '',
                frame.text,
                flags=re.IGNORECASE
            ).strip()

            if cleaned_text != frame.text:
                frame = TextFrame(cleaned_text)

        await self.push_frame(frame, direction)


class CodeFormatter(FrameProcessor):
    """Formats hyphenated codes (like M-E-M-1-2-3) for natural TTS pronunciation"""

    DIGIT_WORDS = {
        '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            formatted_text = self._format_hyphenated_codes(frame.text)

            if formatted_text != frame.text:
                frame = TextFrame(formatted_text)

        await self.push_frame(frame, direction)

    def _format_hyphenated_codes(self, text: str) -> str:
        """
        Converts hyphenated codes like 'M-E-M-1-2-3-4-5-6-7-8-9'
        to speakable format: '... M . E . M . one two three, four five six, seven eight nine'
        """
        # Pattern: Find sequences of 4+ characters/digits separated by hyphens
        # This catches codes like M-E-M-1-2-3-4 but avoids false positives like well-known
        pattern = r'\b([A-Z0-9](?:-[A-Z0-9]){3,})\b'

        def replace_code(match):
            code = match.group(1)
            parts = code.split('-')

            # Separate letters and digits
            letters = [p for p in parts if p.isalpha()]
            digits = [p for p in parts if p.isdigit()]

            # Format letters as 'A . B . C'
            letter_part = ' . '.join(letters) if letters else ''

            # Format digits in groups of 3 with word conversion
            digit_groups = []
            for i in range(0, len(digits), 3):
                group = digits[i:i+3]
                group_words = ' '.join([self.DIGIT_WORDS.get(d, d) for d in group])
                digit_groups.append(group_words)

            digit_part = ', '.join(digit_groups) if digit_groups else ''

            # Combine with ellipsis for pause
            if letter_part and digit_part:
                return f"... {letter_part} . {digit_part}"
            elif letter_part:
                return f"... {letter_part}"
            else:
                return f"... {digit_part}"

        return re.sub(pattern, replace_code, text)

