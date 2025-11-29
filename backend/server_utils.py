import os
import logging
from typing import Union
import aiohttp
from pydantic import BaseModel
from pipecat.runner.daily import DailyRoomConfig, configure
from fastapi import HTTPException

logger = logging.getLogger(__name__)


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


async def create_daily_room(phone_number: str, session: aiohttp.ClientSession) -> DailyRoomConfig:
    try:
        return await configure(session, sip_caller_phone=phone_number)
    except Exception as e:
        logger.error(f"Error creating Daily room: {e}")
        raise Exception(f"Failed to create Daily room: {str(e)}")


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
            raise HTTPException(status_code=500, detail=f"Failed to start bot via Pipecat Cloud: {error_text}")
        logger.info("Bot started successfully via Pipecat Cloud")


async def start_bot_local(
    bot_request: Union[BotRequest, DialinBotRequest],
    session: aiohttp.ClientSession
):
    local_bot_url = os.getenv("LOCAL_BOT_URL", "http://localhost:7860")
    logger.debug(f"Starting bot via local /start endpoint for session {bot_request.session_id}")
    body_data = bot_request.model_dump(exclude_none=True)

    async with session.post(
        f"{local_bot_url}/start",
        headers={"Content-Type": "application/json"},
        json={"createDailyRoom": False, "body": body_data},
    ) as response:
        if response.status != 200:
            error_text = await response.text()
            raise HTTPException(status_code=500, detail=f"Failed to start bot via local /start endpoint: {error_text}")
        logger.info("Bot started successfully via local /start endpoint")
