import re

from loguru import logger
from pipecat.utils.text.base_text_filter import BaseTextFilter


class FunctionCallTextFilter(BaseTextFilter):

    FUNCTION_PATTERNS = [
        # function=name>{"arg": "value"} format
        re.compile(r'function=\w+>\s*\{.*?\}', re.DOTALL),
        # function_name {"arg": "value"} format (Groq Llama)
        re.compile(r'\b(set_\w+|save_\w+|get_\w+|update_\w+|create_\w+|delete_\w+|confirm_\w+|proceed_\w+|end_\w+|schedule_\w+|correct_\w+)\s*\{.*?\}', re.DOTALL),
        # <function_call>...</function_call> XML format
        re.compile(r'<function_call>.*?</function_call>', re.DOTALL),
        # {"function": "name", ...} JSON format
        re.compile(r'\{"function":\s*"[^"]+",?\s*.*?\}', re.DOTALL),
        # tool_call or function_call references
        re.compile(r'\b(tool_call|function_call)\s*[=:]\s*\w+', re.IGNORECASE),
        # Any word followed by JSON object that looks like function args
        re.compile(r'\b\w+_\w+\s*\{\s*"[^"]+"\s*:', re.DOTALL),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def filter(self, text: str) -> str:
        result = text
        for pattern in self.FUNCTION_PATTERNS:
            match = pattern.search(result)
            if match:
                logger.warning(f"Filtered function call from TTS text: '{match.group()}'")
                result = pattern.sub('', result)

        result = re.sub(r'\s+', ' ', result).strip()
        return result
