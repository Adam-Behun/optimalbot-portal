from fastapi import APIRouter

from backend.database import check_connection

router = APIRouter()


@router.get("/")
async def root():
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
            "calls": "/start-call"
        },
        "documentation": "/docs"
    }


@router.get("/health")
async def health():
    is_connected, db_status = await check_connection()

    return {
        "status": "healthy" if is_connected else "degraded",
        "service": "healthcare-backend-api",
        "database": db_status
    }
