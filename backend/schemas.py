from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
import re


class PatientCreate(BaseModel):
    workflow: str = Field(..., min_length=1)
    model_config = {"extra": "allow"}

    @field_validator('workflow')
    @classmethod
    def validate_workflow(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('Workflow is required')
        return v.strip()


class PatientUpdate(BaseModel):
    model_config = {"extra": "allow"}


class BulkPatientRequest(BaseModel):
    patients: List[PatientCreate] = Field(..., max_length=1000)


class CallRequest(BaseModel):
    patient_id: str = Field(..., min_length=24, max_length=24)
    client_name: str = Field(default="prior_auth")
    phone_number: Optional[str] = None

    @field_validator('patient_id')
    @classmethod
    def validate_object_id(cls, v: str) -> str:
        if not re.match(r'^[a-f0-9]{24}$', v):
            raise ValueError('Invalid patient_id format')
        return v


class PatientResponse(BaseModel):
    status: str
    patient_id: str
    message: str


class BulkUploadResponse(BaseModel):
    status: str
    success_count: int
    failed_count: int
    total: int
    created_ids: List[str]
    errors: Optional[List[dict]] = None


class ErrorResponse(BaseModel):
    error: str
    request_id: str
