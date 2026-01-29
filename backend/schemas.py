import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


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
    client_name: str = Field(default="eligibility_verification")
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


class DialinSettings(BaseModel):
    call_id: str
    call_domain: str
    caller_phone: str = Field(alias="from")
    called_phone: str = Field(alias="to")

    model_config = {"populate_by_name": True}


class DialoutTarget(BaseModel):
    phone_number: str = Field(alias="phoneNumber")
    caller_id: Optional[str] = Field(default=None, alias="callerId")

    model_config = {"populate_by_name": True, "by_alias": True}


class TransferConfig(BaseModel):
    staff_phone: str
    caller_id: Optional[str] = None
    enabled: bool = True


class BotBodyData(BaseModel):
    session_id: str
    patient_id: Optional[str] = None  # None for dial-in (patient found/created by flow)
    call_data: dict  # Renamed from patient_data - contains session/caller info
    organization_id: str
    organization_slug: str
    client_name: str
    dialin_settings: Optional[DialinSettings] = None
    dialout_targets: Optional[List[DialoutTarget]] = None
    transfer_config: Optional[TransferConfig] = None
    room_url: Optional[str] = None  # local dev only
    token: Optional[str] = None  # local dev only

    def model_post_init(self, __context):
        has_dialin = self.dialin_settings is not None
        has_dialout = self.dialout_targets is not None
        if not has_dialin and not has_dialout:
            raise ValueError("Either dialin_settings or dialout_targets required")
        if has_dialin and has_dialout:
            raise ValueError("Cannot specify both dialin_settings and dialout_targets")
