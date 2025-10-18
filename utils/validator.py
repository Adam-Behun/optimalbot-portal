"""
Patient data validation for CSV uploads and form submissions
"""
from typing import Dict, Tuple
from datetime import datetime
import re

REQUIRED_FIELDS = [
    'patient_name',
    'date_of_birth',
    'insurance_member_id',
    'insurance_company_name',
    'insurance_phone',
    'facility_name',
    'cpt_code',
    'provider_npi',
    'provider_name',
    'appointment_time'
]

def validate_phone_format(phone: str) -> bool:
    """Validate phone format: +1234567890 (+ followed by 10-15 digits)"""
    pattern = r'^\+\d{10,15}$'
    return bool(re.match(pattern, phone))

def validate_date_format(date_str: str) -> bool:
    """Validate date format: YYYY-MM-DD"""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False

def validate_datetime_format(datetime_str: str) -> bool:
    """Validate datetime format: YYYY-MM-DDTHH:MM or similar ISO format"""
    try:
        if 'T' in datetime_str:
            datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        else:
            datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
        return True
    except ValueError:
        return False

def validate_patient_data(patient_dict: Dict) -> Tuple[bool, str]:
    """
    Validate patient data dictionary
    
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    # Check all required fields are present
    missing_fields = [field for field in REQUIRED_FIELDS if field not in patient_dict or not patient_dict[field]]
    if missing_fields:
        return False, f"Missing required fields: {', '.join(missing_fields)}"
    
    # Validate patient_name
    if not isinstance(patient_dict['patient_name'], str) or len(patient_dict['patient_name'].strip()) == 0:
        return False, "patient_name must be a non-empty string"
    
    # Validate date_of_birth
    if not validate_date_format(patient_dict['date_of_birth']):
        return False, "date_of_birth must be in YYYY-MM-DD format"
    
    # Validate insurance_member_id
    if not isinstance(patient_dict['insurance_member_id'], str) or len(patient_dict['insurance_member_id'].strip()) == 0:
        return False, "insurance_member_id must be a non-empty string"
    
    # Validate insurance_company_name
    if not isinstance(patient_dict['insurance_company_name'], str) or len(patient_dict['insurance_company_name'].strip()) == 0:
        return False, "insurance_company_name must be a non-empty string"
    
    # Validate insurance_phone
    if not validate_phone_format(patient_dict['insurance_phone']):
        return False, "insurance_phone must be in format +1234567890"
    
    # Validate facility_name
    if not isinstance(patient_dict['facility_name'], str) or len(patient_dict['facility_name'].strip()) == 0:
        return False, "facility_name must be a non-empty string"
    
    # Validate cpt_code
    if not isinstance(patient_dict['cpt_code'], str) or len(patient_dict['cpt_code'].strip()) == 0:
        return False, "cpt_code must be a non-empty string"
    
    # Validate provider_npi
    if not isinstance(patient_dict['provider_npi'], str) or len(patient_dict['provider_npi'].strip()) == 0:
        return False, "provider_npi must be a non-empty string"
    
    # Validate provider_name
    if not isinstance(patient_dict['provider_name'], str) or len(patient_dict['provider_name'].strip()) == 0:
        return False, "provider_name must be a non-empty string"
    
    # Validate appointment_time
    if not validate_datetime_format(patient_dict['appointment_time']):
        return False, "appointment_time must be in valid datetime format (YYYY-MM-DDTHH:MM)"
    
    return True, ""