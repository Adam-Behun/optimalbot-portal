import os
import re
import logging
import asyncio
from typing import Union
import aiohttp
from pydantic import BaseModel
from pipecat.runner.daily import DailyRoomConfig, configure
from fastapi import HTTPException

logger = logging.getLogger(__name__)

BOT_START_TIMEOUT = int(os.getenv("BOT_START_TIMEOUT", "30"))
DAILY_ROOM_TIMEOUT = int(os.getenv("DAILY_ROOM_TIMEOUT", "15"))

PHONE_PATTERN = re.compile(r'^\+?1?\d{10,15}$')


class BotRequestBase(BaseModel):
    room_url: str
    token: str
    session_id: str
    patient_id: str
    patient_data: dict
    client_name: str
    organization_id: str
    organization_slug: str


class BotRequest(BotRequestBase):
    phone_number: str


class DialinBotRequest(BotRequestBase):
    call_id: str
    call_domain: str


def normalize_phone_number(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        digits = '1' + digits
    if not digits.startswith('+'):
        digits = '+' + digits
    return digits


def validate_phone_number(phone: str) -> tuple[bool, str]:
    if not phone:
        return False, "Phone number required"
    normalized = normalize_phone_number(phone)
    if not PHONE_PATTERN.match(normalized.replace('+', '')):
        return False, f"Invalid phone number format: {phone}"
    return True, normalized


async def create_daily_room(phone_number: str, session: aiohttp.ClientSession) -> DailyRoomConfig:
    try:
        return await asyncio.wait_for(
            configure(session, sip_caller_phone=phone_number),
            timeout=DAILY_ROOM_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error(f"Daily room creation timed out after {DAILY_ROOM_TIMEOUT}s")
        raise HTTPException(status_code=504, detail="Daily room creation timed out")
    except Exception as e:
        logger.error(f"Error creating Daily room: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create Daily room: {str(e)}")


async def start_bot_production(
    bot_request: Union[BotRequest, DialinBotRequest],
    session: aiohttp.ClientSession
):
    pipecat_api_key = os.getenv("PIPECAT_API_KEY")
    agent_name = os.getenv("PIPECAT_AGENT_NAME", "healthcare-voice-ai")

    if not pipecat_api_key:
        raise HTTPException(status_code=500, detail="PIPECAT_API_KEY required for production mode")

    logger.debug(f"Starting bot via Pipecat Cloud for session {bot_request.session_id}")
    body_data = bot_request.model_dump(exclude_none=True)

    try:
        async with asyncio.timeout(BOT_START_TIMEOUT):
            async with session.post(
                f"https://api.pipecat.daily.co/v1/public/{agent_name}/start",
                headers={
                    "Authorization": f"Bearer {pipecat_api_key}",
                    "Content-Type": "application/json",
                },
                json={"createDailyRoom": False, "body": body_data},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise HTTPException(status_code=500, detail=f"Pipecat Cloud error: {error_text}")
                logger.info("Bot started successfully via Pipecat Cloud")
    except asyncio.TimeoutError:
        logger.error(f"Pipecat Cloud timed out after {BOT_START_TIMEOUT}s")
        raise HTTPException(status_code=504, detail="Bot startup timed out")


async def start_bot_local(
    bot_request: Union[BotRequest, DialinBotRequest],
    session: aiohttp.ClientSession
):
    local_bot_url = os.getenv("LOCAL_BOT_URL", "http://localhost:7860")
    logger.debug(f"Starting bot via local /start endpoint for session {bot_request.session_id}")
    body_data = bot_request.model_dump(exclude_none=True)

    try:
        async with asyncio.timeout(BOT_START_TIMEOUT):
            async with session.post(
                f"{local_bot_url}/start",
                headers={"Content-Type": "application/json"},
                json={"createDailyRoom": False, "body": body_data},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise HTTPException(status_code=500, detail=f"Local bot error: {error_text}")
                logger.info("Bot started successfully via local server")
    except asyncio.TimeoutError:
        logger.error(f"Local bot timed out after {BOT_START_TIMEOUT}s")
        raise HTTPException(status_code=504, detail="Local bot startup timed out")
    except aiohttp.ClientConnectorError:
        logger.error(f"Cannot connect to local bot at {local_bot_url}")
        raise HTTPException(status_code=503, detail=f"Local bot server not running at {local_bot_url}")
