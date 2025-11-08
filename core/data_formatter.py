import logging
import re
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class DataFormatter:

    DIGIT_WORDS = {
        '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
    }

    NATO_PHONETIC = {
        'A': 'Alpha', 'B': 'Bravo', 'C': 'Charlie', 'D': 'Delta',
        'E': 'Echo', 'F': 'Foxtrot', 'G': 'Golf', 'H': 'Hotel',
        'I': 'India', 'J': 'Juliet', 'K': 'Kilo', 'L': 'Lima',
        'M': 'Mike', 'N': 'November', 'O': 'Oscar', 'P': 'Papa',
        'Q': 'Quebec', 'R': 'Romeo', 'S': 'Sierra', 'T': 'Tango',
        'U': 'Uniform', 'V': 'Victor', 'W': 'Whiskey', 'X': 'X-ray',
        'Y': 'Yankee', 'Z': 'Zulu'
    }

    SMART_DEFAULTS = {
        'date_of_birth': 'natural_date',
        'insurance_member_id': 'member_id',
        'provider_npi': 'npi',
        'cpt_code': 'code_digits',
        'provider_name': 'formal_name',
        'appointment_time': 'natural_date',
    }

    PATTERN_FORMATS = [
        (r'.*_date$', 'natural_date'),
        (r'.*_time$', 'natural_date'),
        (r'.*member.*id', 'member_id'),
        (r'.*_npi$', 'npi'),
        (r'.*_code$', 'code_digits'),
        (r'.*_name$', 'formal_name'),
        (r'.*_phone.*', 'phone'),
        (r'.*reference.*', 'nato_phonetic'),
        (r'.*confirmation.*', 'nato_phonetic'),
    ]

    def __init__(self, schema):
        self.schema = schema
        self.overrides = getattr(schema.data_schema, 'speech_overrides', {})

    FIELD_MAPPINGS = {
        "facility_name": "facility",
        "insurance_company_name": "insurance_company"
    }

    def format_patient_data(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        formatted_data = patient_data.copy()

        # Normalize database field names to schema field names
        for db_field, schema_field in self.FIELD_MAPPINGS.items():
            if db_field in formatted_data:
                formatted_data[schema_field] = formatted_data[db_field]

        # Create a snapshot of keys to iterate over (avoid "dict changed size during iteration")
        for field_name, value in list(formatted_data.items()):
            if value is None:
                continue

            format_type = self._determine_format(field_name)
            if format_type:
                spoken_value = self._apply_format(value, format_type, field_name)
                formatted_data[f"{field_name}_spoken"] = spoken_value

        return formatted_data

    def _determine_format(self, field_name: str) -> Optional[str]:
        if field_name in self.overrides:
            return self.overrides[field_name]

        if field_name in self.SMART_DEFAULTS:
            return self.SMART_DEFAULTS[field_name]

        field_lower = field_name.lower()
        for pattern, format_type in self.PATTERN_FORMATS:
            if re.match(pattern, field_lower):
                return format_type

        return None

    def _apply_format(self, value: Any, format_type: str, field_name: str) -> str:
        if value is None:
            return "not available"

        formatters = {
            'natural_date': self._format_natural_date,
            'member_id': self._format_member_id,
            'npi': self._format_npi,
            'code_digits': self._format_code_digits,
            'formal_name': self._format_formal_name,
            'phone': self._format_phone,
            'nato_phonetic': self._format_nato_phonetic,
        }

        formatter = formatters.get(format_type)
        if formatter:
            return formatter(value)

        return str(value)

    def _format_member_id(self, value: str) -> str:
        clean_code = re.sub(r'[^A-Z0-9]', '', str(value).upper())
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

    def _format_npi(self, value: str) -> str:
        clean_code = re.sub(r'[^0-9]', '', str(value))
        digits = list(clean_code)

        if len(digits) == 10:
            group1 = ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits[0:3]])
            group2 = ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits[3:6]])
            group3 = ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits[6:10]])
            return f"{group1}, {group2}, {group3}"

        return ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits])

    def _format_code_digits(self, value: str) -> str:
        clean_code = re.sub(r'[^0-9]', '', str(value))
        digits = list(clean_code)
        return ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits])

    def _format_formal_name(self, value: str) -> str:
        result = str(value)
        result = re.sub(r'\bDr\.?\s*', 'Doctor ', result, flags=re.IGNORECASE)
        result = re.sub(r'\bMr\.?\s*', 'Mister ', result, flags=re.IGNORECASE)
        result = re.sub(r'\bMrs\.?\s*', 'Missus ', result, flags=re.IGNORECASE)
        result = re.sub(r'\bMs\.?\s*', 'Miss ', result, flags=re.IGNORECASE)
        return result.strip()

    def _format_phone(self, value: str) -> str:
        clean_phone = re.sub(r'[^0-9]', '', str(value))
        digits = list(clean_phone)
        return ' '.join([self.DIGIT_WORDS.get(d, d) for d in digits])

    def _format_nato_phonetic(self, value: str) -> str:
        clean_code = re.sub(r'[^A-Z0-9]', '', str(value).upper())
        result = []

        for char in clean_code:
            if char.isalpha():
                result.append(f"{char} as in {self.NATO_PHONETIC.get(char, char)}")
            elif char.isdigit():
                result.append(self.DIGIT_WORDS.get(char, char))

        return ', '.join(result)

    def _format_natural_date(self, value: str) -> str:
        try:
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"]:
                try:
                    date_obj = datetime.strptime(str(value), fmt)
                    month = date_obj.strftime("%B")
                    day = self._ordinal(date_obj.day)
                    year = str(date_obj.year)
                    return f"{month} {day}, {year}"
                except ValueError:
                    continue
            return str(value)
        except Exception:
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
