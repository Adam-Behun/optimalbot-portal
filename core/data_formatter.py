import logging
import re
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class DataFormatter:
    """Formats patient data for natural TTS pronunciation"""

    DIGIT_WORDS = {
        '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
    }

    def __init__(self, schema):
        self.preformat_rules = schema.data_schema.preformat_rules

    def format_patient_data(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        formatted_data = patient_data.copy()

        for field_name, rule in self.preformat_rules.items():
            if field_name in patient_data:
                formatted_value = self._apply_format_rule(patient_data[field_name], rule, field_name)
                formatted_data[f"{field_name}_spoken"] = formatted_value

        return formatted_data

    def _apply_format_rule(self, value: Any, rule, field_name: str = "") -> str:
        if value is None:
            return "not available"

        if rule.format == "natural_speech":
            return self._format_date(value)
        elif rule.format == "spoken_format":
            return self._format_for_speech(value, field_name)

        return str(value)

    def _format_for_speech(self, code: str, field_name: str) -> str:
        """Format codes with letters as 'A . B . C' and digits as word groups"""

        if field_name == "provider_name":
            # Normalize abbreviations for natural speech
            result = str(code)
            result = re.sub(r'\bDr\.?\s*', 'Doctor ', result, flags=re.IGNORECASE)
            result = re.sub(r'\bMr\.?\s*', 'Mister ', result, flags=re.IGNORECASE)
            result = re.sub(r'\bMrs\.?\s*', 'Missus ', result, flags=re.IGNORECASE)
            result = re.sub(r'\bMs\.?\s*', 'Miss ', result, flags=re.IGNORECASE)
            return result.strip()

        clean_code = re.sub(r'[^A-Z0-9]', '', str(code).upper())

        if field_name == "provider_npi":
            # NPI: 10 digits grouped as 3-3-4 with words
            digits = list(clean_code)
            if len(digits) == 10:
                group1 = ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits[0:3]])
                group2 = ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits[3:6]])
                group3 = ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits[6:10]])
                return f"{group1}, {group2}, {group3}"
            return ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits])

        elif field_name == "cpt_code":
            # CPT: Individual digits as words
            digits = list(clean_code)
            return ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits])

        else:
            # Member ID: Letters as 'A . B . C', digits grouped in 3s as words
            chars = list(clean_code)
            letters = [c for c in chars if c.isalpha()]
            digits = [c for c in chars if c.isdigit()]

            letter_part = ' . '.join(letters) if letters else ''

            digit_groups = []
            for i in range(0, len(digits), 3):
                group = digits[i:i+3]
                group_words = ' '.join([self.DIGIT_WORDS.get(d, d) for d in group])
                digit_groups.append(group_words)

            digit_part = ', '.join(digit_groups) if digit_groups else ''

            if letter_part and digit_part:
                return f"{letter_part} . {digit_part}"
            elif letter_part:
                return letter_part
            else:
                return digit_part

    def _format_date(self, value: str) -> str:
        try:
            for fmt in ["%Y-%m-%d", "%m/%d/%Y"]:
                try:
                    date_obj = datetime.strptime(str(value), fmt)
                    month = date_obj.strftime("%B")
                    day = self._ordinal(date_obj.day)
                    year = str(date_obj.year)
                    return f"{month} {day}, {year}"
                except ValueError:
                    continue
            return str(value)
        except:
            return str(value)

    def _ordinal(self, n: int) -> str:
        ordinals = {
            1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
            6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
            11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
            15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
            19: "nineteenth", 20: "twentieth", 21: "twenty-first", 22: "twenty-second",
            23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth",
            26: "twenty-sixth", 27: "twenty-seventh", 28: "twenty-eighth",
            29: "twenty-ninth", 30: "thirtieth", 31: "thirty-first"
        }
        return ordinals.get(n, str(n))