"""
Data Formatter - Pre-formats patient data for voice pronunciation.
Runs once per call at initialization to minimize latency.
"""

import logging
import time
from typing import Dict, Any, Optional
from datetime import datetime
from .schema_loader import ConversationSchema, PreformatRule

logger = logging.getLogger(__name__)


class DataFormatter:
    """
    Formats patient data according to schema preformat_rules.
    Pre-computes all formatted versions at call initialization.
    """
    
    # NATO phonetic alphabet (class constant, loaded once)
    NATO_MAP = {
        'A': 'Alpha', 'B': 'Bravo', 'C': 'Charlie', 'D': 'Delta',
        'E': 'Echo', 'F': 'Foxtrot', 'G': 'Golf', 'H': 'Hotel',
        'I': 'India', 'J': 'Juliet', 'K': 'Kilo', 'L': 'Lima',
        'M': 'Mike', 'N': 'November', 'O': 'Oscar', 'P': 'Papa',
        'Q': 'Quebec', 'R': 'Romeo', 'S': 'Sierra', 'T': 'Tango',
        'U': 'Uniform', 'V': 'Victor', 'W': 'Whiskey', 'X': 'X-ray',
        'Y': 'Yankee', 'Z': 'Zulu'
    }
    
    def __init__(self, schema: ConversationSchema):
        self.schema = schema
        self.preformat_rules = schema.data_schema.preformat_rules
    
    def format_patient_data(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply all preformat rules to patient data.
        Creates new fields with '_spoken' suffix for formatted versions.
        
        Args:
            patient_data: Raw patient data from database
        
        Returns:
            Enhanced patient data with formatted fields
        """
        start_time = time.perf_counter()
        
        # Create a copy to avoid mutating original
        formatted_data = patient_data.copy()
        
        # Apply each preformat rule
        for field_name, rule in self.preformat_rules.items():
            if field_name in patient_data:
                original_value = patient_data[field_name]
                
                # Format based on rule type
                formatted_value = self._apply_format_rule(
                    original_value, 
                    rule
                )
                
                # Store formatted version with _spoken suffix
                formatted_data[f"{field_name}_spoken"] = formatted_value
                
                logger.debug(
                    f"Formatted {field_name}: '{original_value}' → '{formatted_value}'"
                )
        
        # Track formatting time
        format_time_ms = (time.perf_counter() - start_time) * 1000
        
        if format_time_ms > 50:
            logger.warning(
                f"Data formatting took {format_time_ms:.2f}ms (target: <50ms)"
            )
        else:
            logger.debug(f"Data formatting completed in {format_time_ms:.2f}ms")
        
        return formatted_data
    
    def _apply_format_rule(self, value: Any, rule: PreformatRule) -> str:
        """
        Apply a specific formatting rule to a value.
        
        Args:
            value: Original value
            rule: Formatting rule to apply
        
        Returns:
            Formatted string for voice
        """
        if value is None or value == "N/A":
            return "not available"
        
        format_type = rule.format
        
        if format_type == "natural_speech":
            return self._format_natural_speech(value)
        
        elif format_type == "spell_out":
            return self._format_spell_out(value)
        
        elif format_type == "nato_alphabet":
            return self._format_nato(value)
        
        elif format_type == "individual_digits":
            return self._format_individual_digits(value)
        
        elif format_type == "grouped_digits":
            return self._format_grouped_digits(value, rule.grouping)
        
        else:
            logger.warning(f"Unknown format type: {format_type}, returning original")
            return str(value)
    
    def _format_natural_speech(self, value: str) -> str:
        """
        Format date for natural speech.
        Input: "1980-01-15" or "01/15/1980"
        Output: "January fifteenth, nineteen eighty"
        """
        try:
            # Try to parse various date formats
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"]:
                try:
                    date_obj = datetime.strptime(str(value), fmt)
                    break
                except ValueError:
                    continue
            else:
                # If no format worked, return as-is
                return str(value)
            
            # Format for natural speech
            month = date_obj.strftime("%B")  # Full month name
            day = self._ordinal(date_obj.day)
            year = self._year_to_speech(date_obj.year)
            
            return f"{month} {day}, {year}"
            
        except Exception as e:
            logger.warning(f"Error formatting date '{value}': {e}")
            return str(value)
    
    def _format_spell_out(self, value: str) -> str:
        """
        Spell out each character individually.
        Input: "ABC123"
        Output: "A B C 1 2 3"
        """
        # Convert to string and remove spaces/dashes
        value_str = str(value).replace(" ", "").replace("-", "")
        
        # Spell out each character with spaces
        return " ".join(list(value_str))
    
    def _format_nato(self, value: str) -> str:
        """
        Convert to NATO phonetic alphabet.
        Input: "ABC123"
        Output: "Alpha Bravo Charlie 1 2 3"
        """
        result = []
        
        for char in str(value).upper():
            if char.isalpha():
                # Use NATO alphabet for letters
                result.append(self.NATO_MAP.get(char, char))
            elif char.isdigit():
                # Keep digits as-is
                result.append(char)
            elif char not in [' ', '-', '_']:
                # Skip common separators, keep other chars
                result.append(char)
        
        return " ".join(result)
    
    def _format_individual_digits(self, value: str) -> str:
        """
        Format as individual digits.
        Input: "99213"
        Output: "9 9 2 1 3"
        """
        # Convert to string and extract only digits
        digits = ''.join(c for c in str(value) if c.isdigit())
        
        # Space-separate each digit
        return " ".join(list(digits))
    
    def _format_grouped_digits(self, value: str, grouping: Optional[list] = None) -> str:
        """
        Format digits in groups.
        Input: "1234567890", grouping=[3, 3, 4]
        Output: "123 456 7890"
        """
        # Extract only digits
        digits = ''.join(c for c in str(value) if c.isdigit())
        
        if not grouping:
            # Default: group in pairs
            grouping = [2] * (len(digits) // 2)
        
        # Apply grouping
        result = []
        pos = 0
        
        for group_size in grouping:
            if pos >= len(digits):
                break
            result.append(digits[pos:pos + group_size])
            pos += group_size
        
        # Add any remaining digits
        if pos < len(digits):
            result.append(digits[pos:])
        
        return " ".join(result)
    
    def _ordinal(self, n: int) -> str:
        """Convert number to ordinal (1 → first, 2 → second, etc.)"""
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
    
    def _year_to_speech(self, year: int) -> str:
        """
        Convert year to natural speech.
        Input: 1980
        Output: "nineteen eighty"
        """
        if year < 1000 or year > 2099:
            # For unusual years, just say the full number
            return str(year)
        
        if year >= 2000 and year < 2010:
            # Special case: 2000-2009 → "two thousand [one/two/etc]"
            if year == 2000:
                return "two thousand"
            else:
                ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
                return f"two thousand {ones[year - 2000]}"
        
        if year >= 2010:
            # 2010+ → "twenty [ten/eleven/etc]"
            decade = year // 10 % 10
            ones = year % 10
            
            decade_names = {
                1: "twenty", 2: "twenty", 3: "thirty", 4: "forty",
                5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"
            }
            
            ones_names = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
            
            if ones == 0:
                return f"{decade_names[decade]} {decade}0".replace(f"{decade}0", str(year % 100))
            
            # For simplicity with 2010+, just split: "twenty fifteen"
            first_part = str(year)[:2]
            second_part = str(year)[2:]
            
            first = "twenty" if first_part == "20" else first_part
            
            return f"{first} {second_part}" if int(second_part) > 0 else first
        
        # For 1900s: split into two pairs "nineteen eighty"
        first_two = year // 100
        last_two = year % 100
        
        tens_names = {
            0: "", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
            14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen",
            18: "eighteen", 19: "nineteen", 20: "twenty", 30: "thirty",
            40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
            80: "eighty", 90: "ninety"
        }
        
        ones_names = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        
        # First two digits
        if first_two == 19:
            first_word = "nineteen"
        elif first_two == 18:
            first_word = "eighteen"
        else:
            first_word = str(first_two)
        
        # Last two digits
        if last_two == 0:
            second_word = "hundred"
            return f"{first_word} {second_word}".strip()
        elif last_two < 10:
            second_word = f"oh {ones_names[last_two]}"
        elif last_two < 20:
            second_word = tens_names[last_two]
        else:
            tens = (last_two // 10) * 10
            ones = last_two % 10
            if ones == 0:
                second_word = tens_names[tens]
            else:
                second_word = f"{tens_names[tens]} {ones_names[ones]}"
        
        return f"{first_word} {second_word}"