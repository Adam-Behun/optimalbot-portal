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
    """Wraps numeric codes in SSML prosody tags for slower, clearer TTS pronunciation"""

    # Patterns for codes that need slow pronunciation
    CODE_PATTERNS = [
        (r'\bNPI[:\s]+(\d+)', 'NPI'),
        (r'\bMember ID[:\s]+([A-Z0-9\-\s]+)', 'Member ID'),
        (r'\bCPT code[:\s]+(\d+)', 'CPT'),
        (r'\bdate of birth[:\s]+([^,.\n]+)', 'DOB'),
    ]

    SPELL_OUT_TRIGGERS = [
        r'can you spell',
        r'spell that',
        r'say that slower',
        r'slower please',
        r'repeat that',
        r"didn't catch that"
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_codes = {}  # Store last spoken codes for spell-out requests
        self.spell_out_mode = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            text = frame.text

            # Check for spell-out triggers in user direction
            if direction == FrameDirection.UPSTREAM:
                if any(re.search(trigger, text, re.IGNORECASE) for trigger in self.SPELL_OUT_TRIGGERS):
                    self.spell_out_mode = True

            # Apply SSML formatting to assistant responses
            if direction == FrameDirection.DOWNSTREAM:
                formatted_text = self._apply_ssml(text)
                if formatted_text != text:
                    frame = TextFrame(formatted_text)
                self.spell_out_mode = False  # Reset after response

        await self.push_frame(frame, direction)

    def _apply_ssml(self, text: str) -> str:
        """Apply SSML prosody tags to numeric codes"""
        result = text

        for pattern, code_type in self.CODE_PATTERNS:
            matches = list(re.finditer(pattern, result, re.IGNORECASE))

            for match in reversed(matches):  # Reverse to preserve indices
                code_value = match.group(1).strip()
                self.last_codes[code_type] = code_value

                # Format based on spell-out mode
                if self.spell_out_mode:
                    # Character-by-character pronunciation
                    ssml_code = self._format_spell_out(code_value)
                else:
                    # Slow pronunciation with digit spacing
                    ssml_code = self._format_slow_digits(code_value)

                # Replace the code portion with SSML-wrapped version
                result = result[:match.start(1)] + ssml_code + result[match.end(1):]

        return result

    def _format_slow_digits(self, code: str) -> str:
        """Format code with slow prosody and breaks between digit groups"""
        # Extract only alphanumeric characters
        clean_code = re.sub(r'[^A-Z0-9]', '', code.upper())

        # Add pauses between characters for clarity
        spaced = '-'.join(clean_code)

        return f'<prosody rate="0.7">{spaced}</prosody>'

    def _format_spell_out(self, code: str) -> str:
        """Format code for character-by-character spelling"""
        clean_code = re.sub(r'[^A-Z0-9]', '', code.upper())
        return f'<say-as interpret-as="characters">{clean_code}</say-as>'