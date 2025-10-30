"""Health check endpoint"""
from fastapi import APIRouter, Depends

from backend.dependencies import get_patient_db
from backend.models import AsyncPatientRecord

router = APIRouter()


@router.get("/health")
async def health_check(patient_db: AsyncPatientRecord = Depends(get_patient_db)):
    """Health check for backend API"""
    try:
        # Simple DB connectivity check
        await patient_db.find_patients_by_status("Pending")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "service": "healthcare-backend-api",
        "database": db_status
    }
