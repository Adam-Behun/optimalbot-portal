import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from bson import ObjectId
from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)


def convert_objectid(doc: Union[Dict, List, Any]) -> Union[Dict, List, Any]:
    if doc is None:
        return doc

    if isinstance(doc, ObjectId):
        return str(doc)

    if isinstance(doc, list):
        return [convert_objectid(item) for item in doc]

    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if isinstance(value, ObjectId):
                result[key] = str(value)
            elif isinstance(value, dict):
                result[key] = convert_objectid(value)
            elif isinstance(value, list):
                result[key] = convert_objectid(value)
            else:
                result[key] = value

        if "_id" in result:
            result["patient_id"] = result["_id"]

        return result

    return doc


def parse_natural_date(date_str: str, default_year: Optional[int] = None) -> Optional[str]:
    """
    Parse natural language date to ISO format (YYYY-MM-DD).

    Examples:
        "December 3rd" → "2025-12-03"
        "next Tuesday" → "2025-12-02"
        "January 5th" → "2025-01-05"
        "1990-01-15" → "1990-01-15"

    Returns None if parsing fails, returns original string for already valid dates.
    """
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()

    # If already ISO format, return as-is
    if len(date_str) == 10 and date_str[4] == '-' and date_str[7] == '-':
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return date_str
        except ValueError:
            pass

    try:
        # Use current year as default for dates without year
        if default_year is None:
            default_year = datetime.now(timezone.utc).year

        # Create a default date with current year for parsing dates like "December 3rd"
        default_date = datetime(default_year, 1, 1)

        parsed = dateutil_parser.parse(date_str, default=default_date, fuzzy=True)
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, TypeError) as e:
        logger.warning(f"Could not parse date '{date_str}': {e}")
        return None


def parse_natural_time(time_str: str) -> Optional[str]:
    """
    Parse natural language time to 24-hour format (HH:MM).

    Examples:
        "10:30 AM" → "10:30"
        "3:30 PM" → "15:30"
        "9:00 AM" → "09:00"
        "14:00" → "14:00"

    Returns None if parsing fails.
    """
    if not time_str or not time_str.strip():
        return None

    time_str = time_str.strip()

    # If already in HH:MM format, validate and return
    if len(time_str) == 5 and time_str[2] == ':':
        try:
            datetime.strptime(time_str, "%H:%M")
            return time_str
        except ValueError:
            pass

    try:
        # Parse with dateutil and extract time
        parsed = dateutil_parser.parse(time_str, fuzzy=True)
        return parsed.strftime("%H:%M")
    except (ValueError, TypeError) as e:
        logger.warning(f"Could not parse time '{time_str}': {e}")
        return None


def normalize_appointment_datetime(
    date_str: Optional[str],
    time_str: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """
    Normalize appointment date and time to standard formats.

    Returns:
        tuple of (date_iso, time_24h) or (None, None) if parsing fails
    """
    normalized_date = parse_natural_date(date_str) if date_str else None
    normalized_time = parse_natural_time(time_str) if time_str else None
    return normalized_date, normalized_time
