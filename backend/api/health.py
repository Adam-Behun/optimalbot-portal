from fastapi import APIRouter, Depends

from backend.dependencies import get_patient_db
from backend.models import AsyncPatientRecord

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
async def health_check(patient_db: AsyncPatientRecord = Depends(get_patient_db)):
    try:
        await patient_db.patients.find_one({})
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "service": "healthcare-backend-api",
        "database": db_status
    }
