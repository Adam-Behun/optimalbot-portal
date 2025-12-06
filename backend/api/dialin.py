import os
import uuid
import logging
from datetime import datetime, timezone
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from backend.models import get_async_patient_db
from backend.models.organization import get_async_organization_db
from backend.sessions import get_async_session_db
from backend.schemas import DialinSettings, BotBodyData, TransferConfig
from backend.server_utils import create_daily_room, start_bot_production, start_bot_local
from backend.constants import CallStatus

logger = logging.getLogger(__name__)
router = APIRouter()

ENV = os.getenv("ENV", "local")

_processing_calls: TTLCache[str, bool] = TTLCache(maxsize=1000, ttl=300)


class DailyCallData(BaseModel):
    from_phone: str
    to_phone: str
    call_id: str
    call_domain: str


async def call_data_from_request(request: Request) -> DailyCallData:
    data = await request.json()
    logger.info(f"Received Daily webhook data: {data}")

    if not all(key in data for key in ["From", "To", "callId", "callDomain"]):
        raise HTTPException(
            status_code=400,
            detail="Missing properties 'From', 'To', 'callId', 'callDomain'"
        )

    return DailyCallData(
        from_phone=str(data.get("From")),
        to_phone=str(data.get("To")),
        call_id=data.get("callId"),
        call_domain=data.get("callDomain")
    )


@router.post("/dialin-webhook/{client_name}/{workflow_name}")
async def handle_dialin_webhook(client_name: str, workflow_name: str, request: Request) -> JSONResponse:
    logger.info(f"=== DIAL-IN WEBHOOK RECEIVED ===")
    logger.info(f"Client: {client_name}, Workflow: {workflow_name}")

    call_data = await call_data_from_request(request)

    if call_data.call_id in _processing_calls:
        logger.warning(f"Duplicate webhook ignored: {call_data.call_id}")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "already_processing", "call_id": call_data.call_id}
        )
    _processing_calls[call_data.call_id] = True

    org_db = get_async_organization_db()
    organization = await org_db.get_by_slug(client_name)

    if not organization:
        logger.error(f"Organization not found for slug: {client_name}")
        raise HTTPException(status_code=404, detail=f"Organization '{client_name}' not found")

    organization_id = str(organization["_id"])
    logger.info(f"Organization found: {organization.get('name')} (ID: {organization_id})")
    logger.info(f"From: {call_data.from_phone}, To: {call_data.to_phone}")
    logger.info(f"Call ID: {call_data.call_id}, Domain: {call_data.call_domain}")

    session_id = str(uuid.uuid4())
    http_session = request.app.state.http_session

    patient_db = get_async_patient_db()
    patient_data = {
        "workflow": workflow_name,
        "caller_phone_number": call_data.from_phone,
        "organization_id": organization_id,
        "organization_name": organization.get("name", ""),
        "call_status": CallStatus.IN_PROGRESS.value
    }
    patient_id = await patient_db.add_patient(patient_data)

    if not patient_id:
        raise HTTPException(status_code=500, detail="Failed to create patient record")

    logger.info(f"Patient record created: {patient_id}")

    session_db = get_async_session_db()
    session_created = await session_db.create_session({
        "session_id": session_id,
        "patient_id": patient_id,
        "phone_number": call_data.from_phone,
        "client_name": f"{client_name}/{workflow_name}",
        "organization_id": organization_id,
        "call_type": "dial-in"
    })

    if not session_created:
        raise HTTPException(status_code=500, detail="Failed to create session record")

    logger.info(f"Session record created: {session_id}")

    transfer_config = None
    staff_phone = organization.get("staff_phone")
    if staff_phone:
        transfer_config = TransferConfig(
            staff_phone=staff_phone,
            caller_id=organization.get("phone_number_id")
        )

    body_data = BotBodyData(
        session_id=session_id,
        patient_id=patient_id,
        patient_data={
            "patient_id": patient_id,
            "caller_phone": call_data.from_phone,
            "called_number": call_data.to_phone,
            "call_type": "dial-in",
            "workflow": workflow_name,
            "organization_name": organization.get("name", ""),
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        client_name=workflow_name,
        organization_id=organization_id,
        organization_slug=client_name,
        dialin_settings=DialinSettings(
            call_id=call_data.call_id,
            call_domain=call_data.call_domain,
            caller_phone=call_data.from_phone,
            called_phone=call_data.to_phone
        ),
        transfer_config=transfer_config
    )

    try:
        if ENV == "production":
            await start_bot_production(body_data, http_session)
        else:
            daily_config = await create_daily_room(call_data.from_phone, http_session)
            body_data.room_url = daily_config.room_url
            body_data.token = daily_config.token
            await start_bot_local(body_data, http_session)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting dial-in bot: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start bot: {str(e)}")

    logger.info(f"Dial-in bot started for session {session_id}")

    return JSONResponse({
        "status": "success",
        "session_id": session_id
    })
