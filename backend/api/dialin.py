import os
import uuid
import asyncio
from datetime import datetime, timezone
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger
from backend.models.organization import get_async_organization_db
from backend.sessions import get_async_session_db
from backend.schemas import DialinSettings, BotBodyData, TransferConfig
from backend.server_utils import create_daily_room, start_bot_production, start_bot_local
from backend.utils import mask_id, mask_phone

router = APIRouter()

ENV = os.getenv("ENV", "local")

_processing_calls: TTLCache[str, bool] = TTLCache(maxsize=1000, ttl=300)
_background_tasks: set = set()  # prevent GC of background tasks


class DailyCallData(BaseModel):
    from_phone: str
    to_phone: str
    call_id: str
    call_domain: str


async def call_data_from_request(request: Request) -> DailyCallData:
    data = await request.json()
    logger.debug("Received Daily webhook data")  # Don't log raw data with PHI

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
async def handle_dialin_webhook(
    client_name: str, workflow_name: str, request: Request
) -> JSONResponse:
    logger.info(f"Dial-in webhook - client={client_name}, workflow={workflow_name}")

    call_data = await call_data_from_request(request)

    # Fast in-memory dedup (same instance, handles Daily retries)
    if call_data.call_id in _processing_calls:
        logger.warning(f"Duplicate webhook ignored (cache): {call_data.call_id}")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "already_processing", "call_id": call_data.call_id}
        )
    _processing_calls[call_data.call_id] = True

    # MongoDB dedup (cross-instance, handles multi-server deployments)
    session_db = get_async_session_db()
    existing_session = await session_db.find_by_call_id(call_data.call_id)
    if existing_session:
        logger.warning(f"Duplicate webhook ignored (db): {call_data.call_id}")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "already_processing", "call_id": call_data.call_id}
        )

    org_db = get_async_organization_db()
    organization = await org_db.get_by_slug(client_name)

    if not organization:
        logger.error(f"Organization not found for slug: {client_name}")
        raise HTTPException(status_code=404, detail=f"Organization '{client_name}' not found")

    organization_id = str(organization["_id"])
    caller = mask_phone(call_data.from_phone)
    logger.info(f"Org found - id={mask_id(organization_id)}, caller={caller}")

    session_id = str(uuid.uuid4())
    http_session = request.app.state.http_session

    # Create session only - patient lookup/creation handled by flow
    # Store call_id for dedup across restarts/instances
    session_created = await session_db.create_session({
        "session_id": session_id,
        "call_id": call_data.call_id,  # For dedup
        "patient_id": None,  # Flow will find/create patient
        "phone_number": call_data.from_phone,
        "client_name": f"{client_name}/{workflow_name}",
        "workflow": workflow_name,
        "organization_id": organization_id,
        "call_type": "dial-in"
    })

    if not session_created:
        raise HTTPException(status_code=500, detail="Failed to create session record")

    logger.info(f"Session created: {mask_id(session_id)}")

    transfer_config = None
    staff_phone = organization.get("staff_phone")
    if staff_phone:
        transfer_config = TransferConfig(
            staff_phone=staff_phone,
            caller_id=organization.get("phone_number_id")
        )

    body_data = BotBodyData(
        session_id=session_id,
        patient_id=None,  # Flow will find/create patient
        call_data={
            "session_id": session_id,
            "caller_phone": call_data.from_phone,
            "called_phone": call_data.to_phone,
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

    # Start bot in background - respond to Daily immediately to prevent timeout/retry
    async def start_bot_background():
        try:
            if ENV == "production":
                await start_bot_production(body_data, http_session)
            else:
                daily_config = await create_daily_room(call_data.from_phone, http_session)
                body_data.room_url = daily_config.room_url
                body_data.token = daily_config.token
                await start_bot_local(body_data, http_session)
            logger.info(f"Dial-in bot started - session={mask_id(session_id)}")
        except Exception as e:
            logger.error(f"Error starting dial-in bot: {e}")
            # Update session status to failed
            try:
                await session_db.update_session(session_id, {
                    "status": "failed",
                    "error": str(e)
                }, organization_id)
            except Exception:
                pass

    # Schedule background task and prevent GC
    task = asyncio.create_task(start_bot_background())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info(f"Dial-in webhook processed - session={mask_id(session_id)}")

    return JSONResponse({
        "status": "success",
        "session_id": session_id
    })
