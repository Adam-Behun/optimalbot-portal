import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel
from slowapi import Limiter

from backend.constants import CallStatus, SessionStatus
from backend.dependencies import (
    get_current_user,
    get_current_user_organization_id,
    get_patient_db,
    get_session_db,
    get_user_id_from_request,
    log_phi_access,
    require_organization_access,
)
from backend.models import AsyncPatientRecord
from backend.schemas import BotBodyData, CallRequest, DialoutTarget, TransferConfig
from backend.server_utils import (
    create_daily_room,
    start_bot_local,
    start_bot_production,
    validate_phone_number,
)
from backend.sessions import AsyncSessionRecord
from backend.utils import convert_objectid, mask_email, mask_id, mask_phone

router = APIRouter()
limiter = Limiter(key_func=get_user_id_from_request)

ENV = os.getenv("ENV", "local")


class CallResponse(BaseModel):
    status: str
    session_id: str
    room_name: str
    room_url: str
    message: str


@router.post("/start-call")
@limiter.limit("10/minute")
async def start_call(
    call_request: CallRequest,
    request: Request,
    org_context: dict = Depends(require_organization_access),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    current_user = org_context["user"]
    org = org_context["organization"]
    org_id = org_context["organization_id"]

    patient_masked = mask_id(call_request.patient_id)
    user_masked = mask_email(current_user['email'])
    logger.info(f"Initiating call - patient={patient_masked}, user={user_masked}")

    try:
        workflows = org.get("workflows", {})
        if call_request.client_name not in workflows:
            raise HTTPException(
                status_code=400,
                detail=f"Workflow '{call_request.client_name}' not found for this organization"
            )
        if not workflows[call_request.client_name].get("enabled", False):
            raise HTTPException(
                status_code=400,
                detail=f"Workflow '{call_request.client_name}' is not enabled for this organization"
            )

        patient = await patient_db.find_patient_by_id(
            call_request.patient_id, organization_id=org_id
        )
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        patient = convert_objectid(patient)

        await log_phi_access(
            request=request,
            user=current_user,
            action="start_call",
            resource_type="call",
            resource_id=call_request.patient_id
        )

        raw_phone = call_request.phone_number or patient.get("phone_number")
        valid, phone_result = validate_phone_number(raw_phone)
        if not valid:
            raise HTTPException(status_code=400, detail=phone_result)
        phone_number = phone_result

        session_id = str(uuid.uuid4())
        logger.info(f"Call session={mask_id(session_id)}, phone={mask_phone(phone_number)}")

        http_session = request.app.state.http_session
        try:
            phone_number_id = org.get("phone_number_id") or os.getenv("DAILY_PHONE_NUMBER_ID")

            transfer_config = None
            staff_phone = org.get("staff_phone")
            if staff_phone:
                transfer_config = TransferConfig(
                    staff_phone=staff_phone,
                    caller_id=phone_number_id
                )

            body_data = BotBodyData(
                session_id=session_id,
                patient_id=call_request.patient_id,
                call_data=patient,  # For dial-out, call_data contains the patient record
                client_name=call_request.client_name,
                organization_id=str(org_id),
                organization_slug=org.get("slug"),
                dialout_targets=[
                    DialoutTarget(
                        phone_number=phone_number,
                        caller_id=phone_number_id
                    )
                ],
                transfer_config=transfer_config
            )

            if ENV == "production":
                await start_bot_production(body_data, http_session)
            else:
                daily_config = await create_daily_room(phone_number, http_session)
                body_data.room_url = daily_config.room_url
                body_data.token = daily_config.token
                await start_bot_local(body_data, http_session)

            room_url = body_data.room_url or "created-by-pipecat-cloud"
            logger.info(f"Bot started successfully in {ENV.upper()} mode")

            await patient_db.update_call_status(
                call_request.patient_id, CallStatus.DIALING.value, org_id
            )

            await session_db.create_session({
                "session_id": session_id,
                "patient_id": call_request.patient_id,
                "phone_number": phone_number,
                "client_name": call_request.client_name,
                "workflow": call_request.client_name,
                "organization_id": org_id,
                "room_url": room_url,
                "status": SessionStatus.RUNNING.value,
                "call_type": "dial-out"
            })

        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Error starting bot")
            raise HTTPException(status_code=500, detail=f"Failed to start call: {str(e)}")

        return CallResponse(
            status="initiated",
            session_id=session_id,
            room_name=f"call_{session_id}",
            room_url=room_url,
            message=f"Call session initiated ({ENV} mode)"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error initiating call")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/call/{session_id}/status")
async def get_call_status(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    session = await session_db.find_session(session_id, organization_id=org_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    patient = await patient_db.find_patient_by_id(session["patient_id"], organization_id=org_id)

    await log_phi_access(
        request=request,
        user=current_user,
        action="view_status",
        resource_type="call",
        resource_id=session_id
    )

    return {
        "session_id": session_id,
        "status": session.get("status"),
        "patient_name": patient.get("patient_name") if patient else None,
        "call_status": patient.get("call_status") if patient else None,
        "created_at": session.get("created_at"),
        "pid": session.get("pid")
    }


@router.get("/call/{session_id}/transcript")
async def get_call_transcript(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    session = await session_db.find_session(session_id, organization_id=org_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    patient = await patient_db.find_patient_by_id(session["patient_id"], organization_id=org_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    await log_phi_access(
        request=request,
        user=current_user,
        action="view_transcript",
        resource_type="transcript",
        resource_id=session_id
    )

    return {
        "session_id": session_id,
        "transcripts": session.get("call_transcript", {}).get("messages", []),
        "patient_name": patient.get("patient_name") if patient else None
    }


@router.delete("/call/{session_id}")
async def end_call(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    session = await session_db.find_session(session_id, organization_id=org_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    await log_phi_access(
        request=request,
        user=current_user,
        action="end_call",
        resource_type="call",
        resource_id=session_id
    )

    logger.info(f"User {mask_email(current_user['email'])} ending session {mask_id(session_id)}")

    await session_db.update_session(
        session_id, {"status": SessionStatus.TERMINATED.value}, organization_id=org_id
    )

    return {
        "status": "ended",
        "session_id": session_id,
        "message": "Call ended successfully"
    }
