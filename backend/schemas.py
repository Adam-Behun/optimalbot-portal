"""
Pydantic schemas for request/response validation
Validation rules match frontend for consistent UX
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime, timedelta
import re


class PatientCreate(BaseModel):
    """Schema for creating a new patient record"""

    # Patient demographics
    patient_name: str = Field(..., min_length=1, max_length=100)
    date_of_birth: str

    # Insurance information
    insurance_member_id: str = Field(..., min_length=1, max_length=50)
    insurance_company_name: str = Field(..., min_length=1, max_length=100)
    insurance_phone: str
    supervisor_phone: Optional[str] = None

    # Facility/provider information
    facility_name: str = Field(..., min_length=1, max_length=100)
    cpt_code: str
    provider_npi: str
    provider_name: str = Field(..., min_length=1, max_length=100)

    # Appointment information
    appointment_time: str

    # Authorization tracking (optional, populated after call)
    authorization_number: Optional[str] = Field(None, max_length=50)

    # Status fields with defaults
    call_status: Optional[str] = Field(default="Not Started")
    prior_auth_status: Optional[str] = Field(default="Pending")

    @field_validator('patient_name', 'facility_name', 'insurance_company_name', 'provider_name')
    @classmethod
    def validate_text_field(cls, v: str) -> str:
        """Validate text contains only English alphabet characters"""
        if not v or not v.strip():
            raise ValueError('Field cannot be empty')
        # Allow letters, spaces, hyphens, apostrophes, periods, commas
        if not re.match(r"^[a-zA-Z\s\-'.,]+$", v.strip()):
            raise ValueError('Field must contain only English alphabet characters')
        return v.strip()

    @field_validator('insurance_member_id')
    @classmethod
    def validate_member_id(cls, v: str) -> str:
        """Validate member ID is alphanumeric"""
        if not v or not v.strip():
            raise ValueError('Member ID cannot be empty')
        if not re.match(r'^[a-zA-Z0-9]+$', v.strip()):
            raise ValueError('Member ID must contain only letters and numbers')
        return v.strip()

    @field_validator('insurance_phone')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Validate US/Canada phone number format"""
        if not v or not v.strip():
            raise ValueError('Phone number is required')

        # Remove spaces and dashes for validation
        cleaned = v.strip().replace(' ', '').replace('-', '')

        # Must start with +1 or 1
        if cleaned.startswith('+1'):
            digits = cleaned[2:]
        elif cleaned.startswith('1'):
            digits = cleaned[1:]
            cleaned = '+' + cleaned
        else:
            raise ValueError('Phone must start with +1 or 1 (e.g., +15551234567 or 15551234567)')

        # Check remaining are 10 digits
        if not re.match(r'^\d{10}$', digits):
            raise ValueError('Phone must have exactly 10 digits after country code (e.g., +15551234567)')

        # Return normalized format
        return cleaned if cleaned.startswith('+') else '+' + cleaned

    @field_validator('supervisor_phone')
    @classmethod
    def validate_supervisor_phone(cls, v: Optional[str]) -> Optional[str]:
        """Validate supervisor phone (optional, same rules as insurance_phone)"""
        if v is None or v.strip() == '':
            return None

        # Apply same validation as insurance_phone
        cleaned = v.strip().replace(' ', '').replace('-', '')

        if cleaned.startswith('+1'):
            digits = cleaned[2:]
        elif cleaned.startswith('1'):
            digits = cleaned[1:]
            cleaned = '+' + cleaned
        else:
            raise ValueError('Phone must start with +1 or 1 (e.g., +15551234567 or 15551234567)')

        if not re.match(r'^\d{10}$', digits):
            raise ValueError('Phone must have exactly 10 digits after country code (e.g., +15551234567)')

        return cleaned if cleaned.startswith('+') else '+' + cleaned

    @field_validator('cpt_code')
    @classmethod
    def validate_cpt_code(cls, v: str) -> str:
        """Validate CPT code is integers only"""
        if not v or not v.strip():
            raise ValueError('CPT code is required')
        if not re.match(r'^\d+$', v.strip()):
            raise ValueError('CPT code must contain only integers')
        return v.strip()

    @field_validator('provider_npi')
    @classmethod
    def validate_npi(cls, v: str) -> str:
        """Validate NPI is integers only"""
        if not v or not v.strip():
            raise ValueError('Provider NPI is required')
        if not re.match(r'^\d+$', v.strip()):
            raise ValueError('Provider NPI must contain only integers')
        return v.strip()

    @field_validator('date_of_birth')
    @classmethod
    def validate_dob(cls, v: str) -> str:
        """Validate date is at least yesterday and in the past, store in MM/DD/YYYY format"""
        try:
            # Accept both formats for input
            if '/' in v:
                dob = datetime.strptime(v, '%m/%d/%Y')
            else:
                dob = datetime.strptime(v, '%Y-%m-%d')

            yesterday = datetime.now() - timedelta(days=1)
            yesterday = yesterday.replace(hour=23, minute=59, second=59)

            if dob > yesterday:
                raise ValueError('Date of birth must be at least yesterday or earlier')

            # Store in MM/DD/YYYY format for better pronunciation
            return dob.strftime('%m/%d/%Y')
        except ValueError as e:
            if 'does not match format' in str(e):
                raise ValueError('Date must be in YYYY-MM-DD or MM/DD/YYYY format')
            raise

    @field_validator('appointment_time')
    @classmethod
    def validate_appointment_time(cls, v: str) -> str:
        """Validate appointment is between 1 hour from now and 3 months out, store in MM/DD/YYYY HH:MM AM/PM format"""
        try:
            # Try parsing ISO format with T
            if 'T' in v:
                appt = datetime.fromisoformat(v.replace('Z', '+00:00'))
            else:
                # Try space-separated format
                appt = datetime.strptime(v, '%Y-%m-%d %H:%M:%S')

            now = datetime.now()
            min_time = now + timedelta(hours=1)
            max_time = now + timedelta(days=90)  # 3 months

            if appt < min_time:
                raise ValueError('Appointment must be at least 1 hour from now')

            if appt > max_time:
                raise ValueError('Appointment must be within 3 months from now')

            # Store in MM/DD/YYYY HH:MM AM/PM format for better pronunciation
            return appt.strftime('%m/%d/%Y %I:%M %p')
        except ValueError as e:
            if 'does not match' in str(e) or 'Invalid' in str(e):
                raise ValueError('Appointment time must be in valid datetime format (YYYY-MM-DDTHH:MM)')
            raise


class BulkPatientRequest(BaseModel):
    """Schema for bulk patient upload"""
    patients: List[PatientCreate] = Field(..., max_length=1000)


class CallRequest(BaseModel):
    """Schema for initiating a call"""
    patient_id: str = Field(..., min_length=24, max_length=24)
    client_name: str = Field(default="prior_auth")
    phone_number: Optional[str] = None

    @field_validator('patient_id')
    @classmethod
    def validate_object_id(cls, v: str) -> str:
        """Validate MongoDB ObjectId format"""
        if not re.match(r'^[a-f0-9]{24}$', v):
            raise ValueError('Invalid patient_id format')
        return v


class PatientResponse(BaseModel):
    """Standard response (no PHI echoed back)"""
    status: str
    patient_id: str
    message: str


class BulkUploadResponse(BaseModel):
    """Bulk upload response"""
    status: str
    success_count: int
    failed_count: int
    total: int
    created_ids: List[str]
    errors: Optional[List[dict]] = None


class ErrorResponse(BaseModel):
    """Standard error response"""
    error: str
    request_id: str
