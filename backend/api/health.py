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


async def check_daily() -> Tuple[bool, str]:
    """Check Daily.co API connectivity."""
    api_key = os.getenv("DAILY_API_KEY")
    return await _check_service(
        "Daily",
        "https://api.daily.co/v1/",
        {"Authorization": f"Bearer {api_key}"},
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
    """Health check for backend services.

    Only checks services the backend actually uses:
    - MongoDB (database)
    - Daily (call room creation)

    Bot-only services (OpenAI, Deepgram, Cartesia) are not checked here.
    """
    results = await asyncio.gather(
        check_connection(),  # MongoDB
        check_daily(),
        return_exceptions=True
    )

    def process_result(result, service_name: str) -> dict:
        if isinstance(result, Exception):
            logger.error(f"{service_name} health check exception: {result}")
            return {"healthy": False, "status": f"error: {str(result)[:50]}"}
        healthy, status = result
        return {"healthy": healthy, "status": status}

    services = {
        "mongodb": process_result(results[0], "mongodb"),
        "daily": process_result(results[1], "daily"),
    }

    # MongoDB is critical, Daily is important but not blocking
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
