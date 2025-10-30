"""
Pydantic schemas for request/response validation
Minimal validation focused on security and data integrity
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
import re
import phonenumbers


class PatientCreate(BaseModel):
    """Schema for creating a new patient record"""

    # Patient demographics
    patient_name: str = Field(..., min_length=1, max_length=100)
    date_of_birth: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')

    # Insurance information
    insurance_member_id: str = Field(..., min_length=1, max_length=50)
    insurance_company_name: str = Field(..., min_length=1, max_length=100)
    insurance_phone: str

    # Facility/provider information
    facility_name: str = Field(..., min_length=1, max_length=100)
    cpt_code: str = Field(..., min_length=1, max_length=20)
    provider_npi: str = Field(..., pattern=r'^\d{10}$')
    provider_name: str = Field(..., min_length=1, max_length=100)

    # Appointment information
    appointment_time: str

    # Authorization tracking (optional, populated after call)
    authorization_number: Optional[str] = Field(None, max_length=50)

    # Status fields with defaults
    call_status: Optional[str] = Field(default="Not Started")
    prior_auth_status: Optional[str] = Field(default="Pending")

    @field_validator('insurance_phone')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Validate US/Canada phone only, block emergency/premium numbers"""
        try:
            parsed = phonenumbers.parse(v.strip(), None)

            # US/Canada only (+1)
            if parsed.country_code != 1:
                raise ValueError('Only US/Canadian numbers allowed')

            if not phonenumbers.is_valid_number(parsed):
                raise ValueError('Invalid phone number')

            # Block emergency and premium numbers
            national = str(parsed.national_number)
            blocked_prefixes = ['911', '988', '999', '900', '976']
            if any(national.startswith(p) for p in blocked_prefixes):
                raise ValueError('Cannot call emergency/premium numbers')

            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            raise ValueError('Invalid phone format, use +1XXXXXXXXXX')

    @field_validator('date_of_birth')
    @classmethod
    def validate_dob(cls, v: str) -> str:
        """Validate date format and reasonable range"""
        try:
            dob = datetime.strptime(v, '%Y-%m-%d')
            if dob > datetime.now():
                raise ValueError('Date cannot be in future')
            if (datetime.now() - dob).days > 130 * 365:
                raise ValueError('Date too far in past')
            return v
        except ValueError as e:
            if 'format' in str(e).lower():
                raise ValueError('Use YYYY-MM-DD format')
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
