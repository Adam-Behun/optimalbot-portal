"""Utilities for bot management in local and production environments.

This module provides functions for:
- Creating Daily rooms for outgoing calls
- Starting bots in production (Pipecat Cloud) or local development mode
"""

import os
import logging
import aiohttp
from pipecat.runner.daily import DailyRoomConfig, configure

logger = logging.getLogger(__name__)
from pydantic import BaseModel


class BotRequest(BaseModel):
    """Request data sent to bot start endpoint.

    Attributes:
        room_url: Daily room URL for the bot to join
        token: Authentication token for the Daily room
        session_id: Session identifier for tracking
        patient_id: Patient identifier
        patient_data: Full patient record
        phone_number: Phone number to dial
        client_name: Client/flow name (e.g., 'prior_auth')
        organization_id: Organization ID for tenant isolation
        organization_slug: Organization slug for workflow path resolution
    """
    room_url: str
    token: str
    session_id: str
    patient_id: str
    patient_data: dict
    phone_number: str
    client_name: str
    organization_id: str
    organization_slug: str


async def create_daily_room(phone_number: str, session: aiohttp.ClientSession) -> DailyRoomConfig:
    """Create a Daily room configured for PSTN dial-out.

    Args:
        phone_number: Phone number for dial-out
        session: Shared aiohttp session for making HTTP requests

    Returns:
        DailyRoomConfig: Configuration object with room_url and token

    Raises:
        Exception: If room creation fails
    """
    try:
        return await configure(session, sip_caller_phone=phone_number)
    except Exception as e:
        logger.error(f"Error creating Daily room: {e}")
        raise Exception(f"Failed to create Daily room: {str(e)}")


async def start_bot_production(bot_request: BotRequest, session: aiohttp.ClientSession):
    """Start the bot via Pipecat Cloud API for production deployment.

    Args:
        bot_request: Bot configuration with room_url, token, and patient data
        session: Shared aiohttp session for making HTTP requests

    Raises:
        Exception: If required environment variables are missing or API call fails
    """
    pipecat_api_key = os.getenv("PIPECAT_API_KEY")
    agent_name = os.getenv("PIPECAT_AGENT_NAME", "healthcare-voice-ai")

    if not pipecat_api_key:
        raise Exception("PIPECAT_API_KEY required for production mode")

    logger.debug(f"Starting bot via Pipecat Cloud for patient {bot_request.patient_id}")

    body_data = bot_request.model_dump(exclude_none=True)

    async with session.post(
        f"https://api.pipecat.daily.co/v1/public/{agent_name}/start",
        headers={
            "Authorization": f"Bearer {pipecat_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "createDailyRoom": False,  # We already created the room
            "body": body_data,
        },
    ) as response:
        if response.status != 200:
            error_text = await response.text()
            raise Exception(f"Failed to start bot via Pipecat Cloud: {error_text}")
        logger.debug("Bot started successfully via Pipecat Cloud")


async def start_bot_local(bot_request: BotRequest, session: aiohttp.ClientSession):
    """Start the bot via local /start endpoint for development.

    Args:
        bot_request: Bot configuration with room_url, token, and patient data
        session: Shared aiohttp session for making HTTP requests

    Raises:
        Exception: If LOCAL_BOT_URL is not accessible or API call fails
    """
    local_bot_url = os.getenv("LOCAL_BOT_URL", "http://localhost:7860")

    logger.debug(f"Starting bot via local /start endpoint for patient {bot_request.patient_id}")

    body_data = bot_request.model_dump(exclude_none=True)

    async with session.post(
        f"{local_bot_url}/start",
        headers={"Content-Type": "application/json"},
        json={
            "createDailyRoom": False,  # We already created the room
            "body": body_data,
        },
    ) as response:
        if response.status != 200:
            error_text = await response.text()
            raise Exception(f"Failed to start bot via local /start endpoint: {error_text}")
        logger.debug("Bot started successfully via local /start endpoint")
