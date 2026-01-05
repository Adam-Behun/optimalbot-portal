import asyncio
import os
import re
import time

import aiohttp
from fastapi import HTTPException
from loguru import logger

from backend.constants import CallStatus
from backend.resilience import (
    CircuitBreakerConfig,
    CircuitOpenError,
    get_circuit_breaker,
)
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

# Circuit breaker configuration for Pipecat Cloud API
PIPECAT_CIRCUIT_CONFIG = CircuitBreakerConfig(
    name="pipecat_cloud",
    failure_threshold=5,
    recovery_timeout=30.0,
    success_threshold=2,
)


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
    """Create Daily room for local dev. Not used in production."""
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


async def _record_bot_start_failure(
    body_data: BotBodyData,
    failure_type: str,
    error: str,
):
    """Record bot start failure to metrics, update patient status, and send alert."""
    try:
        # Record to metrics
        from backend.metrics import get_metrics_collector
        metrics = get_metrics_collector()
        await metrics.record_call_failure(
            session_id=body_data.session_id,
            error=error,
            stage="bot_start"
        )

        # Update patient status if patient_id is available
        if body_data.patient_id:
            from backend.models import get_async_patient_db
            patient_db = get_async_patient_db()
            await patient_db.update_call_status(
                body_data.patient_id,
                CallStatus.FAILED.value,
                body_data.organization_id
            )
            logger.info(f"Patient {body_data.patient_id} status updated to FAILED")

        # Update session status
        from backend.sessions import get_async_session_db
        session_db = get_async_session_db()
        await session_db.update_session(
            body_data.session_id,
            {"status": "failed", "error": error, "error_type": failure_type},
            body_data.organization_id
        )

        # Send alert
        from backend.alerts import get_email_alerter
        alerter = get_email_alerter()
        await alerter.alert_bot_start_failure(
            session_id=body_data.session_id,
            error=error,
            patient_id=body_data.patient_id
        )

    except Exception as e:
        logger.error(f"Error recording bot start failure: {e}")


async def _start_bot_production_impl(
    body_data: BotBodyData,
    session: aiohttp.ClientSession,
    payload: dict,
    agent_name: str,
    pipecat_api_key: str,
):
    """Internal implementation of Pipecat Cloud API call."""
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

    # Use circuit breaker for Pipecat Cloud API calls
    circuit = get_circuit_breaker("pipecat_cloud", PIPECAT_CIRCUIT_CONFIG)

    try:
        await circuit.call(
            _start_bot_production_impl,
            body_data,
            session,
            payload,
            agent_name,
            pipecat_api_key,
        )
    except CircuitOpenError as e:
        logger.error(f"Pipecat Cloud circuit breaker open: {e}")
        await _record_bot_start_failure(body_data, "circuit_open", str(e))
        raise HTTPException(status_code=503, detail="Bot service temporarily unavailable.")
    except asyncio.TimeoutError:
        logger.error(f"Pipecat Cloud timed out after {BOT_START_TIMEOUT}s")
        await _record_bot_start_failure(body_data, "timeout", "Pipecat Cloud timeout")
        raise HTTPException(status_code=504, detail="Bot startup timed out")
    except HTTPException as e:
        await _record_bot_start_failure(body_data, "api_error", e.detail)
        raise
    except Exception as e:
        logger.error(f"Unexpected error starting bot: {e}")
        await _record_bot_start_failure(body_data, "unknown", str(e))
        raise HTTPException(status_code=500, detail=f"Bot startup failed: {str(e)}")


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
                    await _record_bot_start_failure(body_data, "api_error", error_text)
                    raise HTTPException(status_code=500, detail=f"Local bot error: {error_text}")
                logger.info("Bot started successfully via local server")
    except asyncio.TimeoutError:
        logger.error(f"Local bot timed out after {BOT_START_TIMEOUT}s")
        await _record_bot_start_failure(body_data, "timeout", "Local bot timeout")
        raise HTTPException(status_code=504, detail="Local bot startup timed out")
    except aiohttp.ClientConnectorError:
        logger.error(f"Cannot connect to local bot at {local_bot_url}")
        await _record_bot_start_failure(body_data, "connection_error", local_bot_url)
        raise HTTPException(status_code=503, detail=f"Local bot not running: {local_bot_url}")
    except HTTPException:
        raise  # Re-raise HTTP exceptions (already handled)
    except Exception as e:
        logger.error(f"Unexpected error starting local bot: {e}")
        await _record_bot_start_failure(body_data, "unknown", str(e))
        raise HTTPException(status_code=500, detail=f"Local bot startup failed: {str(e)}")
