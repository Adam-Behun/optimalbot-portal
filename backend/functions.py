import logging

logger = logging.getLogger(__name__)


def convert_spoken_to_numeric(text: str) -> str:
    """Convert spoken numbers to digits (e.g., 'one two three' -> '123')

    This is used for converting spoken reference numbers like "one two three"
    to "123" for database storage.
    """
    if not text:
        return text

    word_to_digit = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
        'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
        'oh': '0'
    }

    parts = text.lower().split()
    converted = [word_to_digit.get(part.strip('.,!?;:-'), part) for part in parts]

    return ''.join(converted)


__all__ = ['convert_spoken_to_numeric']
