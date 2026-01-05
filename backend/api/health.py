"""Health check endpoints with external service monitoring."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional, Tuple

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from backend.database import check_connection
from backend.dependencies import get_current_user

router = APIRouter()

# Timeout for external service checks (seconds)
SERVICE_CHECK_TIMEOUT = 5


async def _check_service(
    service_name: str,
    url: str,
    headers: dict,
    api_key: Optional[str] = None,
    skip_if_local: bool = False
) -> Tuple[bool, str]:
    """Generic health check for external HTTP services.

    Args:
        service_name: Name for logging
        url: URL to check
        headers: Request headers (should include auth)
        api_key: API key to check (returns not configured if missing)
        skip_if_local: Return success in local mode
    """
    if skip_if_local and os.getenv("ENV", "local") == "local":
        return True, "local mode (not applicable)"

    if api_key is None:
        return False, "not configured"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=SERVICE_CHECK_TIMEOUT)
            ) as resp:
                if resp.status == 200:
                    return True, "connected"
                elif resp.status == 401:
                    return False, "invalid API key"
                elif resp.status == 429:
                    return True, "rate limited (service available)"
                return False, f"HTTP {resp.status}"
    except asyncio.TimeoutError:
        return False, "timeout"
    except aiohttp.ClientError as e:
        return False, f"connection error: {str(e)[:50]}"
    except Exception as e:
        logger.error(f"{service_name} health check error: {e}")
        return False, str(e)[:50]


async def check_openai() -> Tuple[bool, str]:
    """Check OpenAI API connectivity."""
    api_key = os.getenv("OPENAI_API_KEY")
    return await _check_service(
        "OpenAI",
        "https://api.openai.com/v1/models",
        {"Authorization": f"Bearer {api_key}"},
        api_key
    )


async def check_deepgram() -> Tuple[bool, str]:
    """Check Deepgram API connectivity."""
    api_key = os.getenv("DEEPGRAM_API_KEY")
    return await _check_service(
        "Deepgram",
        "https://api.deepgram.com/v1/projects",
        {"Authorization": f"Token {api_key}"},
        api_key
    )


async def check_daily() -> Tuple[bool, str]:
    """Check Daily.co API connectivity."""
    api_key = os.getenv("DAILY_API_KEY")
    return await _check_service(
        "Daily",
        "https://api.daily.co/v1/",
        {"Authorization": f"Bearer {api_key}"},
        api_key
    )


async def check_pipecat() -> Tuple[bool, str]:
    """Check Pipecat Cloud API (production only)."""
    api_key = os.getenv("PIPECAT_API_KEY")
    return await _check_service(
        "Pipecat",
        "https://api.pipecat.daily.co/v1/public/agents",
        {"Authorization": f"Bearer {api_key}"},
        api_key,
        skip_if_local=True
    )


async def check_cartesia() -> Tuple[bool, str]:
    """Check Cartesia TTS API connectivity."""
    api_key = os.getenv("CARTESIA_API_KEY")
    return await _check_service(
        "Cartesia",
        "https://api.cartesia.ai/voices",
        {"X-API-Key": api_key, "Cartesia-Version": "2024-06-10"},
        api_key
    )


@router.get("/")
async def root():
    """Root endpoint with service information."""
    return {
        "service": "Healthcare AI Agent - Backend API",
        "version": "2.0",
        "status": "operational",
        "architecture": {
            "backend": "Fly.io (this service)",
            "frontend": "Vercel",
            "bot": "Pipecat Cloud"
        },
        "endpoints": {
            "health": "/health",
            "auth": "/auth/*",
            "patients": "/patients/*",
            "calls": "/start-call",
            "metrics": "/metrics/*"
        },
        "documentation": "/docs"
    }


@router.get("/health")
async def health():
    """Enhanced health check with all service status.

    Returns overall status:
    - healthy: All services operational
    - degraded: Some non-critical services down
    - unhealthy: Critical services down (MongoDB)
    """
    # Run all checks in parallel for efficiency
    results = await asyncio.gather(
        check_connection(),  # MongoDB
        check_openai(),
        check_deepgram(),
        check_daily(),
        check_pipecat(),
        check_cartesia(),
        return_exceptions=True
    )

    # Process results (handle exceptions)
    def process_result(result, service_name: str) -> dict:
        if isinstance(result, Exception):
            logger.error(f"{service_name} health check exception: {result}")
            return {"healthy": False, "status": f"error: {str(result)[:50]}"}
        healthy, status = result
        return {"healthy": healthy, "status": status}

    services = {
        "mongodb": process_result(results[0], "mongodb"),
        "openai": process_result(results[1], "openai"),
        "deepgram": process_result(results[2], "deepgram"),
        "daily": process_result(results[3], "daily"),
        "pipecat": process_result(results[4], "pipecat"),
        "cartesia": process_result(results[5], "cartesia"),
    }

    # Determine overall status
    # Critical: MongoDB must be up
    # Non-critical: External services can be degraded
    mongodb_healthy = services["mongodb"]["healthy"]
    all_healthy = all(s["healthy"] for s in services.values())

    if all_healthy:
        overall = "healthy"
    elif mongodb_healthy:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return {
        "status": overall,
        "service": "healthcare-backend-api",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": services
    }


@router.get("/health/ready")
async def health_ready():
    """Readiness check for load balancers.

    Returns 503 if critical services are down.
    """
    is_connected, db_status = await check_connection()

    if is_connected:
        return {"ready": True, "database": db_status}
    else:
        raise HTTPException(status_code=503, detail=f"Database not ready: {db_status}")


@router.get("/health/detailed")
async def health_detailed(current_user: dict = Depends(get_current_user)):
    """Detailed health check with additional info (authenticated).

    Available to all authenticated users for debugging.
    """
    # Get standard health check
    health_data = await health()

    # Add environment info
    health_data["environment"] = os.getenv("ENV", "local")
    health_data["version"] = os.getenv("APP_VERSION", "unknown")

    return health_data
