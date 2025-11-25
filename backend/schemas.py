"""
Pydantic schemas for request/response validation
Multi-tenant: Accepts flat dynamic fields based on workflow schema
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
import re


class PatientCreate(BaseModel):
    """Schema for creating a new patient record with flat dynamic fields.

    All fields are stored flat in the database (no custom_fields nesting).
    Validation is minimal here - schema validation happens based on org's workflow schema.
    """

    # Required workflow identifier
    workflow: str = Field(..., min_length=1, description="Workflow this patient belongs to")

    # All other fields are dynamic and stored flat
    # Use model_extra to capture any additional fields
    model_config = {"extra": "allow"}

    @field_validator('workflow')
    @classmethod
    def validate_workflow(cls, v: str) -> str:
        """Validate workflow name"""
        if not v or not v.strip():
            raise ValueError('Workflow is required')
        return v.strip()


class PatientUpdate(BaseModel):
    """Schema for updating patient fields - all dynamic"""
    model_config = {"extra": "allow"}


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
