"""Call management endpoints"""
import os
import logging
import traceback
import asyncio
import uuid
import datetime
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from slowapi import Limiter
from bson import ObjectId
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pipecatcloud.session import Session, SessionParams
from pipecatcloud.exception import AgentStartError

from backend.dependencies import (
    get_current_user,
    get_patient_db,
    get_session_db,
    log_phi_access,
    get_user_id_from_request
)
from backend.models import AsyncPatientRecord
from backend.sessions import AsyncSessionRecord
from backend.schemas import CallRequest

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_user_id_from_request)

PIPECAT_TIMEOUT_SECONDS = int(os.getenv("PIPECAT_TIMEOUT_SECONDS", "60"))


# Models
class CallResponse(BaseModel):
    status: str
    session_id: str
    room_name: str
    room_url: str
    message: str


# Helpers
def convert_objectid(doc: dict) -> dict:
    """Convert MongoDB ObjectId to string"""
    if doc and "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
        doc["patient_id"] = doc["_id"]
    return doc


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    reraise=True
)
async def start_pipecat_session_with_retry(session: Session) -> dict:
    """Start Pipecat Cloud session with retry logic"""
    return await asyncio.wait_for(
        session.start(),
        timeout=PIPECAT_TIMEOUT_SECONDS
    )


@router.post("/start-call")
@limiter.limit("10/minute")
async def start_call(
    call_request: CallRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    """Initiate new call session"""
    logger.info("=== INITIATING CALL ===")
    logger.info(f"Patient ID: {call_request.patient_id}")
    logger.info(f"User: {current_user['email']}")

    try:
        # Fetch patient
        logger.info("Fetching patient data from database...")
        patient = await patient_db.find_patient_by_id(call_request.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        patient = convert_objectid(patient)
        logger.info(f"Patient found with ID: {call_request.patient_id}")

        # Log PHI access
        await log_phi_access(
            request=request,
            user=current_user,
            action="start_call",
            resource_type="call",
            resource_id=call_request.patient_id
        )

        # Get phone number
        phone_number = call_request.phone_number or patient.get("phone_number")
        if not phone_number:
            raise HTTPException(status_code=400, detail="Phone number required")

        # Generate session ID
        session_id = str(uuid.uuid4())
        logger.info(f"Session ID: {session_id}")
        logger.info(f"Phone number: {phone_number}")

        # Create session record
        await session_db.create_session({
            "session_id": session_id,
            "patient_id": call_request.patient_id,
            "phone_number": phone_number,
            "client_name": call_request.client_name
        })

        # Start Pipecat Cloud bot
        logger.info("Starting Pipecat Cloud bot session...")

        try:
            agent_name = os.getenv("PIPECAT_AGENT_NAME", "healthcare-voice-ai")
            pipecat_api_key = os.getenv("PIPECAT_API_KEY")

            if not pipecat_api_key:
                raise HTTPException(status_code=500, detail="PIPECAT_API_KEY not configured")

            # Create session with Pipecat Cloud
            session = Session(
                agent_name=agent_name,
                api_key=pipecat_api_key,
                params=SessionParams(
                    use_daily=True,
                    daily_room_properties={
                        "enable_dialout": True,
                        "enable_chat": False,
                        "enable_screenshare": False,
                        "enable_recording": "cloud",
                        "exp": int(datetime.datetime.now().timestamp()) + 3600
                    },
                    data={
                        "patient_id": call_request.patient_id,
                        "patient_data": patient,
                        "phone_number": phone_number,
                        "client_name": call_request.client_name
                    }
                )
            )

            # Start bot with retry
            response = await start_pipecat_session_with_retry(session)

            room_url = response.get("dailyRoom")
            token = response.get("dailyToken")

            logger.info(f"✅ Bot started via Pipecat Cloud")
            logger.info(f"Room: {room_url}")

            # Update session
            await session_db.update_session(session_id, {
                "room_url": room_url,
                "status": "running"
            })

        except AgentStartError as e:
            logger.error(f"Pipecat Cloud start error: {e}")
            await session_db.update_session(session_id, {"status": "failed"})
            raise HTTPException(status_code=500, detail=f"Failed to start bot: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await session_db.update_session(session_id, {"status": "failed"})
            raise HTTPException(status_code=500, detail=f"Failed to start bot: {e}")

        return CallResponse(
            status="initiated",
            session_id=session_id,
            room_name=f"call_{session_id}",
            room_url=room_url,
            message="Call session initiated via Pipecat Cloud"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating call: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/call/{session_id}/status")
async def get_call_status(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    """Get status of active call"""
    session = await session_db.find_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    # Get patient data
    patient = await patient_db.find_patient_by_id(session["patient_id"])

    # Log PHI access
    await log_phi_access(
        request=request,
        user=current_user,
        action="view_status",
        resource_type="call",
        resource_id=session_id
    )

    return {
        "session_id": session_id,
        "status": session.get("status"),
        "patient_name": patient.get("patient_name") if patient else None,
        "call_status": patient.get("call_status") if patient else None,
        "created_at": session.get("created_at"),
        "pid": session.get("pid")
    }


@router.get("/call/{session_id}/transcript")
async def get_call_transcript(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    patient_db: AsyncPatientRecord = Depends(get_patient_db),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    """Get full transcript of call"""
    session = await session_db.find_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    # Get patient with transcript
    patient = await patient_db.find_patient_by_id(session["patient_id"])
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Log PHI access
    await log_phi_access(
        request=request,
        user=current_user,
        action="view_transcript",
        resource_type="transcript",
        resource_id=session_id
    )

    return {
        "session_id": session_id,
        "transcripts": patient.get("call_transcript", {}).get("messages", []),
        "patient_name": patient.get("patient_name")
    }


@router.delete("/call/{session_id}")
async def end_call(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    session_db: AsyncSessionRecord = Depends(get_session_db)
):
    """End active call"""
    session = await session_db.find_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    # Log PHI access
    await log_phi_access(
        request=request,
        user=current_user,
        action="end_call",
        resource_type="call",
        resource_id=session_id
    )

    logger.info(f"User {current_user['email']} ending call session: {session_id}")

    # Bot runs on Pipecat Cloud - managed externally
    await session_db.update_session(session_id, {"status": "terminated"})

    return {
        "status": "ended",
        "session_id": session_id,
        "message": "Call ended successfully"
    }
