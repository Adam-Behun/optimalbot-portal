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