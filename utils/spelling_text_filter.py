import re

from pipecat.utils.text.base_text_filter import BaseTextFilter


class SpellingTextFilter(BaseTextFilter):
    """Detect L-E-E patterns and convert to Cartesia SSML with pauses."""

    HYPHEN_PATTERN = re.compile(r'\b([A-Za-z0-9]-[A-Za-z0-9](?:-[A-Za-z0-9])+)\b')
    COMMA_PATTERN = re.compile(r'\b([A-Za-z0-9],\s*[A-Za-z0-9](?:,\s*[A-Za-z0-9])+)\b')

    def __init__(self, pause_ms: int = 200, **kwargs):
        super().__init__(**kwargs)
        self.pause_ms = pause_ms

    def _expand_to_ssml(self, chars: list[str]) -> str:
        spelled = [f"<spell>{c.upper()}</spell>" for c in chars]
        return f'<break time="{self.pause_ms}ms"/>'.join(spelled)

    async def filter(self, text: str) -> str:
        result = text

        def expand_hyphen(match):
            chars = match.group(1).split('-')
            if all(len(c) == 1 and c.isalnum() for c in chars):
                return self._expand_to_ssml(chars)
            return match.group(0)

        result = self.HYPHEN_PATTERN.sub(expand_hyphen, result)

        def expand_comma(match):
            chars = [c.strip() for c in match.group(1).split(',')]
            if all(len(c) == 1 and c.isalnum() for c in chars):
                return self._expand_to_ssml(chars)
            return match.group(0)

        result = self.COMMA_PATTERN.sub(expand_comma, result)

        return result
