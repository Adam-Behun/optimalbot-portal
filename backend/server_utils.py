import os
import re
import time
import asyncio
import aiohttp
from fastapi import HTTPException
from loguru import logger
from backend.schemas import BotBodyData

# Only import pipecat for local development (not needed in production)
_pipecat_available = False
try:
    from pipecat.runner.daily import DailyRoomConfig, configure
    _pipecat_available = True
except ImportError:
    DailyRoomConfig = None
    configure = None

BOT_START_TIMEOUT = int(os.getenv("BOT_START_TIMEOUT", "30"))
DAILY_ROOM_TIMEOUT = int(os.getenv("DAILY_ROOM_TIMEOUT", "15"))

PHONE_PATTERN = re.compile(r'^\+?1?\d{10,15}$')


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


async def create_daily_room(phone_number: str, session: aiohttp.ClientSession):
    """Create Daily room for local development. Not used in production (Pipecat Cloud creates rooms)."""
    if not _pipecat_available:
        raise HTTPException(
            status_code=500,
            detail="Local room creation requires pipecat-ai. Use ENV=production for Pipecat Cloud."
        )
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


def build_dialin_room_properties(caller_phone: str, expiry_seconds: int = 300) -> dict:
    return {
        "sip": {
            "sip_mode": "dial-in",
            "num_endpoints": 2,
            "display_name": caller_phone
        },
        "enable_dialout": True,
        "exp": int(time.time()) + expiry_seconds
    }


def build_dialout_room_properties(expiry_seconds: int = 300) -> dict:
    return {
        "enable_dialout": True,
        "exp": int(time.time()) + expiry_seconds
    }


async def start_bot_production(body_data: BotBodyData, session: aiohttp.ClientSession):
    pipecat_api_key = os.getenv("PIPECAT_API_KEY")
    agent_name = os.getenv("PIPECAT_AGENT_NAME", "healthcare-voice-ai")

    if not pipecat_api_key:
        raise HTTPException(status_code=500, detail="PIPECAT_API_KEY required for production mode")

    if body_data.dialin_settings:
        daily_room_properties = build_dialin_room_properties(
            caller_phone=body_data.dialin_settings.caller_phone
        )
    else:
        daily_room_properties = build_dialout_room_properties()

    payload = {
        "createDailyRoom": True,
        "dailyRoomProperties": daily_room_properties,
        "body": body_data.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
            exclude={"room_url", "token"}
        )
    }

    logger.debug(f"Starting bot via Pipecat Cloud for session {body_data.session_id}")

    try:
        async with asyncio.timeout(BOT_START_TIMEOUT):
            async with session.post(
                f"https://api.pipecat.daily.co/v1/public/{agent_name}/start",
                headers={
                    "Authorization": f"Bearer {pipecat_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise HTTPException(status_code=500, detail=f"Pipecat Cloud error: {error_text}")
                logger.info("Bot started successfully via Pipecat Cloud")
    except asyncio.TimeoutError:
        logger.error(f"Pipecat Cloud timed out after {BOT_START_TIMEOUT}s")
        raise HTTPException(status_code=504, detail="Bot startup timed out")


async def start_bot_local(body_data: BotBodyData, session: aiohttp.ClientSession):
    if not body_data.room_url or not body_data.token:
        raise ValueError("room_url and token required for local mode")

    local_bot_url = os.getenv("LOCAL_BOT_URL", "http://localhost:7860")
    logger.debug(f"Starting bot via local /start endpoint for session {body_data.session_id}")

    payload = {
        "createDailyRoom": False,
        "body": body_data.model_dump(mode="json", by_alias=True, exclude_none=True)
    }

    try:
        async with asyncio.timeout(BOT_START_TIMEOUT):
            async with session.post(
                f"{local_bot_url}/start",
                headers={"Content-Type": "application/json"},
                json=payload,
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
