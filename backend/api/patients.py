from fastapi import APIRouter, HTTPException, Request, Depends
from slowapi import Limiter
from loguru import logger

from backend.dependencies import (
    get_current_user,
    get_patient_db,
    get_audit_logger_dep,
    get_client_info,
    log_phi_access,
    get_user_id_from_request,
    get_current_user_organization_id
)
from backend.models import AsyncPatientRecord
from backend.audit import AuditLogger
from backend.schemas import PatientCreate, PatientResponse, BulkPatientRequest, BulkUploadResponse
from backend.utils import convert_objectid, mask_id

router = APIRouter()
limiter = Limiter(key_func=get_user_id_from_request)


@router.get("")
async def list_patients(
    request: Request,
    workflow: str = None,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    try:
        logger.info(f"Fetching patients for org, workflow={workflow}")

        all_patients = await patient_db.find_patients_by_organization(org_id, workflow=workflow)

        logger.info(f"🔍 Found {len(all_patients)} patients for organization")

        patients = [convert_objectid(p) for p in all_patients]

        ip_address, user_agent = get_client_info(request)
        await audit_logger.log_phi_access(
            user_id=current_user["sub"],
            action="view_list",
            resource_type="patient",
            resource_id="all",
            ip_address=ip_address,
            user_agent=user_agent,
            endpoint=request.url.path,
            details={"count": len(patients), "workflow": workflow},
            organization_id=org_id
        )

        logger.info(f"✅ Returning {len(patients)} patients for org {org_id}")

        return {
            "patients": patients,
            "total_count": len(patients)
        }

    except Exception as e:
        logger.exception("Error fetching patients")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{patient_id}")
async def get_patient_by_id(
    patient_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db)
):
    try:
        patient = await patient_db.find_patient_by_id(patient_id, organization_id=org_id)

        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        await log_phi_access(
            request=request,
            user=current_user,
            action="view",
            resource_type="patient",
            resource_id=patient_id
        )

        return {
            "status": "success",
            "patient": convert_objectid(patient)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching patient: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=PatientResponse)
@limiter.limit("20/minute")
async def add_patient(
    patient_data: PatientCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db)
):
    patient_dict = patient_data.model_dump(by_alias=True)

    # Include extra dynamic fields (model_config extra="allow")
    if hasattr(patient_data, '__pydantic_extra__') and patient_data.__pydantic_extra__:
        patient_dict.update(patient_data.__pydantic_extra__)

    patient_dict['organization_id'] = org_id

    patient_id = await patient_db.add_patient(patient_dict)

    if not patient_id:
        raise HTTPException(status_code=500, detail="Failed to add patient")

    await log_phi_access(
        request=request,
        user=current_user,
        action="create",
        resource_type="patient",
        resource_id=patient_id
    )

    logger.info(f"Added patient {mask_id(patient_id)} workflow={patient_data.workflow}")

    return PatientResponse(
        status="success",
        patient_id=str(patient_id),
        message="Patient added successfully"
    )


@router.post("/bulk", response_model=BulkUploadResponse)
@limiter.limit("5/hour")
async def add_patients_bulk(
    bulk_request: BulkPatientRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    success_count = 0
    failed_count = 0
    errors = []
    created_ids = []

    for idx, patient_model in enumerate(bulk_request.patients):
        patient_dict = patient_model.model_dump()
        patient_dict['organization_id'] = org_id

        patient_id = await patient_db.add_patient(patient_dict)

        if patient_id:
            success_count += 1
            created_ids.append(patient_id)
            logger.info(f"Bulk add: patient {mask_id(patient_id)} to org {mask_id(org_id)}")
        else:
            failed_count += 1
            errors.append({
                "row": idx + 1,
                "error": "Database insertion failed"
            })

    ip_address, user_agent = get_client_info(request)
    await audit_logger.log_phi_access(
        user_id=current_user["sub"],
        action="create_bulk",
        resource_type="patient",
        resource_id="bulk",
        ip_address=ip_address,
        user_agent=user_agent,
        endpoint=request.url.path,
        details={"success_count": success_count, "failed_count": failed_count},
        organization_id=org_id
    )

    return BulkUploadResponse(
        status="completed",
        success_count=success_count,
        failed_count=failed_count,
        total=len(bulk_request.patients),
        created_ids=created_ids,
        errors=errors if errors else None
    )


@router.put("/{patient_id}")
async def update_patient(
    patient_id: str,
    patient_data: dict,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db)
):
    try:
        success = await patient_db.update_patient(patient_id, patient_data, organization_id=org_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

        await log_phi_access(
            request=request,
            user=current_user,
            action="update",
            resource_type="patient",
            resource_id=patient_id
        )

        return {"status": "success", "message": "Patient updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating patient")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{patient_id}")
async def delete_patient(
    patient_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db)
):
    try:
        success = await patient_db.delete_patient(patient_id, organization_id=org_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

        await log_phi_access(
            request=request,
            user=current_user,
            action="delete",
            resource_type="patient",
            resource_id=patient_id
        )

        return {"status": "success", "message": "Patient deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting patient")
        raise HTTPException(status_code=500, detail=str(e))
