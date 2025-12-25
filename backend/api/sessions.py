from fastapi import APIRouter, HTTPException, Request, Depends
from loguru import logger

from backend.dependencies import (
    get_current_user,
    get_audit_logger_dep,
    get_client_info,
    get_current_user_organization_id
)
from backend.sessions import get_async_session_db, AsyncSessionRecord
from backend.audit import AuditLogger
from backend.utils import convert_objectid

router = APIRouter()


def get_session_db() -> AsyncSessionRecord:
    return get_async_session_db()


@router.get("")
async def list_sessions(
    request: Request,
    workflow: str = None,
    patient_id: str = None,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    try:
        if patient_id:
            sessions = await session_db.find_sessions_by_patient(patient_id, org_id)
        else:
            sessions = await session_db.find_sessions_by_organization(org_id, workflow=workflow)

        sessions = [convert_objectid(s) for s in sessions]

        ip_address, user_agent = get_client_info(request)
        await audit_logger.log_phi_access(
            user_id=current_user["sub"],
            action="view_list",
            resource_type="session",
            resource_id="all",
            ip_address=ip_address,
            user_agent=user_agent,
            endpoint=request.url.path,
            details={"count": len(sessions), "workflow": workflow, "patient_id": patient_id},
            organization_id=org_id
        )

        return {"sessions": sessions, "total_count": len(sessions)}

    except Exception as e:
        logger.exception("Error fetching sessions")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    try:
        session = await session_db.find_session(session_id, org_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        ip_address, user_agent = get_client_info(request)
        await audit_logger.log_phi_access(
            user_id=current_user["sub"],
            action="view",
            resource_type="session",
            resource_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
            endpoint=request.url.path,
            details={},
            organization_id=org_id
        )

        return {"session": convert_objectid(session)}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching session")
        raise HTTPException(status_code=500, detail=str(e))
