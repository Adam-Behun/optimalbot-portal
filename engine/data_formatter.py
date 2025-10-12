import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class DataFormatter:
    def __init__(self, schema):
        self.preformat_rules = schema.data_schema.preformat_rules
    
    def format_patient_data(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        formatted_data = patient_data.copy()
        
        for field_name, rule in self.preformat_rules.items():
            if field_name in patient_data:
                formatted_value = self._apply_format_rule(patient_data[field_name], rule)
                formatted_data[f"{field_name}_spoken"] = formatted_value
        
        return formatted_data
    
    def _apply_format_rule(self, value: Any, rule) -> str:
        if value is None:
            return "not available"
        
        if rule.format == "natural_speech":
            return self._format_date(value)
        elif rule.format == "spell_out":
            return " ".join(str(value).replace(" ", "").replace("-", ""))
        elif rule.format == "individual_digits":
            return " ".join(c for c in str(value) if c.isdigit())
        elif rule.format == "grouped_digits":
            digits = ''.join(c for c in str(value) if c.isdigit())
            groups = rule.grouping or [3, 3, 4]
            result = []
            pos = 0
            for size in groups:
                if pos < len(digits):
                    result.append(digits[pos:pos+size])
                    pos += size
            if pos < len(digits):
                result.append(digits[pos:])
            return " ".join(result)
        
        return str(value)
    
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