from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import AudioRawFrame, Frame, TextFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.audio.utils import create_stream_resampler
import audioop
import re

class AudioResampler(FrameProcessor):
    def __init__(self, target_sample_rate: int = 16000, target_channels: int = 1, **kwargs):
        super().__init__(**kwargs)
        self._resampler = create_stream_resampler()
        self.target_sample_rate = target_sample_rate
        self.target_channels = target_channels

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AudioRawFrame):
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

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AudioRawFrame) and len(frame.audio) == 0:
            return
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


class SSMLCodeFormatter(FrameProcessor):
    """Formats numeric codes with word-based digits and ellipsis padding for clear TTS pronunciation"""

    # Patterns for codes that need slow pronunciation
    CODE_PATTERNS = [
        (r'\bNPI[:\s]+(\d+)', 'NPI'),
        (r'\bMember ID[:\s]+([A-Z0-9\-\s]+)', 'Member ID'),
        (r'\bCPT code[:\s]+(\d+)', 'CPT'),
        (r'\bdate of birth[:\s]+([^,.\n]+)', 'DOB'),
    ]

    # Digit to word mapping for natural pronunciation
    DIGIT_WORDS = {
        '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_codes = {}  # Store last spoken codes for spell-out requests

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            text = frame.text

            # Apply formatting to assistant responses only
            if direction == FrameDirection.DOWNSTREAM:
                formatted_text = self._apply_formatting(text)
                if formatted_text != text:
                    frame = TextFrame(formatted_text)

        await self.push_frame(frame, direction)

    def _apply_formatting(self, text: str) -> str:
        """Apply digit-to-word and ellipsis formatting to codes"""
        result = text

        for pattern, code_type in self.CODE_PATTERNS:
            matches = list(re.finditer(pattern, result, re.IGNORECASE))

            for match in reversed(matches):  # Reverse to preserve indices
                code_value = match.group(1).strip()
                self.last_codes[code_type] = code_value

                # Format the code with digits as words and ellipses
                formatted_code = self._format_code(code_value, code_type)

                # Replace the code portion with formatted version
                result = result[:match.start(1)] + formatted_code + result[match.end(1):]

        return result

    def _format_code(self, code: str, code_type: str) -> str:
        """Format code with digit-to-word conversion and ellipsis padding"""
        # Extract only alphanumeric characters
        clean_code = re.sub(r'[^A-Z0-9]', '', code.upper())

        if code_type == 'NPI':
            # NPI: Group as 3-3-4 with digits as words
            # Example: 1234567890 → "one two three, four five six, seven eight nine zero"
            digits = list(clean_code)
            if len(digits) == 10:
                group1 = ', '.join([self.DIGIT_WORDS.get(d, d) for d in digits[0:3]])
                group2 = ', '.join([self.DIGIT_WORDS.get(d, d) for d in digits[3:6]])
                group3 = ', '.join([self.DIGIT_WORDS.get(d, d) for d in digits[6:10]])
                return f"{group1}, {group2}, {group3}"
            else:
                # Fallback: all digits as words with commas
                return ', '.join([self.DIGIT_WORDS.get(d, d) for d in digits])

        elif code_type == 'CPT':
            # CPT: Individual digits as words
            # Example: 99214 → "nine, nine, two, one, four"
            digits = list(clean_code)
            return ', '.join([self.DIGIT_WORDS.get(d, d) for d in digits])

        else:
            # Member ID and others: Mix of letters and digits with ellipses between each
            # Example: ABC456 → "... ... ... A ... ... ... B ... ... ... C ... ... ... four, five, six"
            chars = list(clean_code)
            result = []

            for char in chars:
                if char.isdigit():
                    # Convert digit to word
                    result.append(self.DIGIT_WORDS.get(char, char))
                else:
                    # Letter: add with ellipsis padding
                    result.append(f"... ... ... {char}")

            return ' '.join(result)