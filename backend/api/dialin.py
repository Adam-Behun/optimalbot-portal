"""Dial-in webhook endpoint for Daily PSTN incoming calls.

This module handles incoming PSTN calls from Daily.co and starts the bot
to handle patient questions workflows.
"""

import os
import uuid
import logging
import aiohttp
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pipecat.runner.daily import configure

logger = logging.getLogger(__name__)
router = APIRouter()

ENV = os.getenv("ENV", "local")


class DailyCallData(BaseModel):
    """Data received from Daily PSTN webhook.

    Attributes:
        from_phone: The caller's phone number
        to_phone: The dialed phone number
        call_id: Unique identifier for the call
        call_domain: Daily domain for the call
    """
    from_phone: str
    to_phone: str
    call_id: str
    call_domain: str


class DialinBotRequest(BaseModel):
    """Request data sent to bot start endpoint for dial-in calls.

    Attributes:
        room_url: Daily room URL for the bot to join
        token: Authentication token for the Daily room
        session_id: Session identifier for tracking
        patient_id: Patient identifier (generated for dial-in)
        patient_data: Patient data (minimal for dial-in)
        client_name: Client name (org_slug/workflow)
        organization_id: Organization ID
        organization_slug: Organization slug
        call_id: Unique identifier for the call
        call_domain: Daily domain for the call
    """
    room_url: str
    token: str
    session_id: str
    patient_id: str
    patient_data: dict
    client_name: str
    organization_id: str
    organization_slug: str
    call_id: str
    call_domain: str


async def call_data_from_request(request: Request) -> DailyCallData:
    """Parse and validate Daily PSTN webhook data from incoming request.

    Args:
        request: FastAPI request object containing webhook data

    Returns:
        DailyCallData: Parsed and validated call data

    Raises:
        HTTPException: If required fields are missing from the webhook data
    """
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


async def create_dialin_room(
    call_data: DailyCallData,
    session: aiohttp.ClientSession
):
    """Create a Daily room configured for PSTN dial-in.

    Args:
        call_data: Call data containing caller phone number
        session: Shared aiohttp session for making HTTP requests

    Returns:
        DailyRoomConfig: Configuration object with room_url and token

    Raises:
        HTTPException: If room creation fails
    """
    try:
        return await configure(session, sip_caller_phone=call_data.from_phone)
    except Exception as e:
        logger.error(f"Error creating Daily room for dial-in: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Daily room: {str(e)}"
        )


async def start_dialin_bot_production(
    bot_request: DialinBotRequest,
    session: aiohttp.ClientSession
):
    """Start the bot via Pipecat Cloud API for dial-in calls.

    Args:
        bot_request: Bot configuration with room_url, token, and call details
        session: Shared aiohttp session for making HTTP requests

    Raises:
        HTTPException: If required env vars are missing or API call fails
    """
    pipecat_api_key = os.getenv("PIPECAT_API_KEY")
    agent_name = os.getenv("PIPECAT_AGENT_NAME", "healthcare-voice-ai")

    if not pipecat_api_key:
        raise HTTPException(
            status_code=500,
            detail="PIPECAT_API_KEY required for production mode"
        )

    logger.info(f"Starting dial-in bot via Pipecat Cloud for call {bot_request.call_id}")

    body_data = bot_request.model_dump(exclude_none=True)

    async with session.post(
        f"https://api.pipecat.daily.co/v1/public/{agent_name}/start",
        headers={
            "Authorization": f"Bearer {pipecat_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "createDailyRoom": False,
            "body": body_data,
        },
    ) as response:
        if response.status != 200:
            error_text = await response.text()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start bot via Pipecat Cloud: {error_text}"
            )
        logger.info("Dial-in bot started successfully via Pipecat Cloud")


async def start_dialin_bot_local(
    bot_request: DialinBotRequest,
    session: aiohttp.ClientSession
):
    """Start the bot via local /start endpoint for dial-in calls.

    Args:
        bot_request: Bot configuration with room_url, token, and call details
        session: Shared aiohttp session for making HTTP requests

    Raises:
        HTTPException: If local server is not accessible
    """
    local_bot_url = os.getenv("LOCAL_BOT_URL", "http://localhost:7860")

    logger.info(f"Starting dial-in bot via local /start for call {bot_request.call_id}")

    body_data = bot_request.model_dump(exclude_none=True)

    async with session.post(
        f"{local_bot_url}/start",
        headers={"Content-Type": "application/json"},
        json={
            "createDailyRoom": False,
            "body": body_data,
        },
    ) as response:
        if response.status != 200:
            error_text = await response.text()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start bot via local endpoint: {error_text}"
            )
        logger.info("Dial-in bot started successfully via local endpoint")


@router.post("/dialin-webhook/{client_name}/{workflow_name}")
async def handle_dialin_webhook(
    client_name: str,
    workflow_name: str,
    request: Request
) -> JSONResponse:
    """Handle incoming Daily PSTN call webhook.

    This endpoint:
    1. Receives Daily webhook data for incoming PSTN calls
    2. Creates a Daily room with dial-in capabilities
    3. Starts the bot (locally or via Pipecat Cloud based on ENV)
    4. Returns room details for the caller

    Args:
        client_name: Organization slug (e.g., 'demo_clinic_alpha')
        workflow_name: Workflow name (e.g., 'patient_questions')
        request: FastAPI request containing Daily webhook data

    Returns:
        JSONResponse: Success status with room_url and token

    Raises:
        HTTPException: If webhook data is invalid or bot fails to start
    """
    logger.info(f"=== DIAL-IN WEBHOOK RECEIVED ===")
    logger.info(f"Client: {client_name}, Workflow: {workflow_name}")

    # Parse Daily webhook data
    call_data = await call_data_from_request(request)

    logger.info(f"From: {call_data.from_phone}, To: {call_data.to_phone}")
    logger.info(f"Call ID: {call_data.call_id}, Domain: {call_data.call_domain}")

    # Generate session and patient IDs for this dial-in call
    session_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())  # For dial-in, we create a temporary patient ID

    # Create Daily room for dial-in
    async with aiohttp.ClientSession() as http_session:
        daily_config = await create_dialin_room(call_data, http_session)

        logger.info(f"Daily room created: {daily_config.room_url}")

        # Build bot request with dial-in specific data
        # Client name format: {org_slug}/{workflow_name}
        full_client_name = f"{client_name}/{workflow_name}"

        bot_request = DialinBotRequest(
            room_url=daily_config.room_url,
            token=daily_config.token,
            session_id=session_id,
            patient_id=patient_id,
            patient_data={
                "caller_phone": call_data.from_phone,
                "called_number": call_data.to_phone,
                "call_type": "dial-in",
                "created_at": datetime.now(timezone.utc).isoformat()
            },
            client_name=full_client_name,
            organization_id=client_name,  # Use org slug as ID for dial-in
            organization_slug=client_name,
            call_id=call_data.call_id,
            call_domain=call_data.call_domain
        )

        # Start bot based on environment
        try:
            if ENV == "production":
                await start_dialin_bot_production(bot_request, http_session)
            else:
                await start_dialin_bot_local(bot_request, http_session)
        except Exception as e:
            logger.error(f"Error starting dial-in bot: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start bot: {str(e)}"
            )

    logger.info(f"✅ Dial-in bot started for session {session_id}")

    return JSONResponse({
        "status": "success",
        "room_url": daily_config.room_url,
        "token": daily_config.token,
        "session_id": session_id
    })
