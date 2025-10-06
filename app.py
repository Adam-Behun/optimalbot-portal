import os
import logging
import traceback
import asyncio
import datetime
import json
import re
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn
import uuid
from typing import Optional

from pipeline import HealthcareAIPipeline
from models import get_async_patient_db

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Healthcare AI Agent", version="1.0.0")

app.mount("/static", StaticFiles(directory="static"), name="static")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
active_pipelines = {}
patient_db = get_async_patient_db()

class CallRequest(BaseModel):
    patient_id: str
    phone_number: Optional[str] = None

class AddPatientRequest(BaseModel):
    patient_name: str
    date_of_birth: str
    insurance_member_id: Optional[str] = None
    insurance_company_name: str
    facility_name: str
    cpt_code: str
    provider_npi: str
    appointment_time: Optional[str] = None

def validate_phone_number(phone: str) -> bool:
    """Validate phone number format (accepts +1XXXXXXXXXX or XXXXXXXXXX format)"""
    # Remove spaces and dashes for validation
    cleaned = phone.replace(' ', '').replace('-', '')
    # Match international format (+1...) or US format (10-15 digits)
    return bool(re.match(r'^\+?1?\d{10,15}$', cleaned))

@app.post("/add-patient")
async def add_patient(request: AddPatientRequest):
    """Add a new patient to the database"""
    try:
        patient_data = request.dict()
        patient_id = await patient_db.add_patient(patient_data)
        
        if not patient_id:
            raise HTTPException(status_code=500, detail="Failed to add patient")
        
        logger.info(f"Added new patient: {patient_data['patient_name']} (ID: {patient_id})")
        
        return {
            "status": "success",
            "patient_id": patient_id,
            "patient_name": patient_data['patient_name'],
            "message": f"Patient {patient_data['patient_name']} added successfully"
        }
        
    except Exception as e:
        logger.error(f"Error adding patient: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to add patient")

@app.post("/start-call")
async def start_call(request: CallRequest):
    """Start outbound call via Daily.co PSTN"""
    try:
        logger.info(f"=== START CALL REQUEST ===")
        logger.info(f"Patient ID: {request.patient_id}")
        logger.info(f"Phone Number: {request.phone_number}")
        
        # Validate patient exists and fetch full record
        patient_data = await patient_db.find_patient_by_id(request.patient_id)
        if not patient_data:
            logger.error(f"Patient not found: {request.patient_id}")
            raise HTTPException(status_code=404, detail=f"Patient not found: {request.patient_id}")
        
        logger.info(f"Found patient: {patient_data.get('patient_name')}")
        
        # Validate phone number
        if not request.phone_number:
            logger.error("Phone number is required but not provided")
            raise HTTPException(status_code=400, detail="Phone number is required")
        
        if not validate_phone_number(request.phone_number):
            logger.error(f"Invalid phone number format: {request.phone_number}")
            raise HTTPException(status_code=400, detail="Invalid phone number format")
        
        # Ensure phone number is in E.164 format (+1XXXXXXXXXX)
        phone_number = request.phone_number.strip()
        if not phone_number.startswith('+'):
            # Assume US number if no country code
            phone_number = f"+1{phone_number.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')}"
        
        logger.info(f"Formatted phone number: {phone_number}")
        
        # Convert _id to string for serialization and LLM use
        patient_data['_id'] = str(patient_data['_id'])
        
        # Generate session ID
        session_id = str(uuid.uuid4())
        
        logger.info(f"Generated session ID: {session_id}")
        logger.info(f"Starting Daily outbound call - session: {session_id}, patient: {patient_data.get('patient_name')}, phone: {phone_number}")
        
        # Update call status to "In Progress" and store phone number
        await patient_db.update_call_info(
            patient_id=request.patient_id,
            call_status="In Progress",
            insurance_phone_number=phone_number
        )
        logger.info("Updated patient call status to 'In Progress'")
        
        # Daily.co API setup
        daily_api_key = os.getenv("DAILY_API_KEY")
        daily_phone_id = os.getenv("DAILY_PHONE_NUMBER_ID")
        
        logger.info(f"Daily API Key present: {bool(daily_api_key)}")
        logger.info(f"Daily Phone Number ID: {daily_phone_id}")
        
        if not daily_api_key or not daily_phone_id:
            logger.error("Missing DAILY_API_KEY or DAILY_PHONE_NUMBER_ID")
            raise HTTPException(status_code=500, detail="Daily.co configuration missing")
        
        headers = {
            "Authorization": f"Bearer {daily_api_key}",
            "Content-Type": "application/json"
        }
        
        # Create Daily room with dial-out enabled
        room_name = f"healthcare-call-{session_id[:8]}"
        expiry_timestamp = int((datetime.datetime.utcnow() + datetime.timedelta(hours=1)).timestamp())
        
        room_payload = {
            "name": room_name,
            "properties": {
                "enable_dialout": True,
                "exp": expiry_timestamp
            }
        }
        
        logger.info(f"=== CREATING DAILY ROOM ===")
        logger.info(f"Room name: {room_name}")
        logger.info(f"Room payload: {json.dumps(room_payload, indent=2)}")
        
        room_response = requests.post(
            "https://api.daily.co/v1/rooms",
            headers=headers,
            json=room_payload,
            timeout=10
        )
        
        logger.info(f"Room creation status code: {room_response.status_code}")
        logger.info(f"Room creation response: {room_response.text}")
        
        if room_response.status_code not in [200, 201]:
            logger.error(f"Failed to create Daily room: {room_response.status_code} - {room_response.text}")
            raise HTTPException(status_code=500, detail=f"Failed to create call room: {room_response.text}")
        
        room_data = room_response.json()
        room_url = room_data["url"]
        
        logger.info(f"✓ Created Daily room: {room_name}, URL: {room_url}")
        
        # Create bot token for Daily room
        token_payload = {
            "properties": {
                "room_name": room_name,
                "is_owner": True
            }
        }
        
        logger.info(f"=== CREATING BOT TOKEN ===")
        logger.info(f"Token payload: {json.dumps(token_payload, indent=2)}")
        
        token_response = requests.post(
            "https://api.daily.co/v1/meeting-tokens",
            headers=headers,
            json=token_payload,
            timeout=10
        )
        
        logger.info(f"Token creation status code: {token_response.status_code}")
        
        if token_response.status_code != 200:
            logger.error(f"Failed to create Daily token: {token_response.status_code} - {token_response.text}")
            raise HTTPException(status_code=500, detail="Failed to create call token")
        
        bot_token = token_response.json()["token"]
        logger.info(f"✓ Created bot token for room {room_name}")
        
        # Create and store pipeline
        logger.info(f"=== CREATING PIPELINE ===")
        pipeline = HealthcareAIPipeline(
            session_id=session_id,
            patient_id=request.patient_id,
            patient_data=patient_data
        )
        active_pipelines[session_id] = pipeline
        logger.info(f"Pipeline created and stored in active_pipelines")
        
        # Start pipeline (bot joins room)
        logger.info(f"=== STARTING PIPELINE ===")
        logger.info(f"Room URL: {room_url}")
        logger.info(f"Room name: {room_name}")
        
        pipeline_task = asyncio.create_task(
            pipeline.run(room_url, bot_token, room_name)
        )
        logger.info("Pipeline task created")
        
        # Wait for bot to join the room before triggering dial-out
        logger.info("Waiting 3 seconds for bot to join room...")
        await asyncio.sleep(3)
        
        # Trigger dial-out via Daily's REST API
        dialout_payload = {
            "phoneNumber": phone_number,
            "callerId": daily_phone_id
        }
        
        logger.info(f"=== TRIGGERING DIALOUT ===")
        logger.info(f"Dialout endpoint: https://api.daily.co/v1/rooms/{room_name}/dialOut/start")
        logger.info(f"Dialout payload: {json.dumps(dialout_payload, indent=2)}")
        
        dialout_response = requests.post(
            f"https://api.daily.co/v1/rooms/{room_name}/dialOut/start",
            headers=headers,
            json=dialout_payload,
            timeout=10
        )
        
        logger.info(f"Dialout status code: {dialout_response.status_code}")
        logger.info(f"Dialout response: {dialout_response.text}")
        
        if dialout_response.status_code not in [200, 201]:
            logger.error(f"Failed to trigger dial-out: {dialout_response.status_code} - {dialout_response.text}")
            raise HTTPException(status_code=500, detail=f"Failed to initiate call: {dialout_response.text}")
        
        dialout_data = dialout_response.json()
        logger.info(f"✓ Dial-out triggered successfully")
        logger.info(f"Dialout data: {json.dumps(dialout_data, indent=2)}")
        
        logger.info(f"=== CALL INITIATED SUCCESSFULLY ===")
        
        return {
            "status": "success",
            "session_id": session_id,
            "patient_id": request.patient_id,
            "patient_name": patient_data.get('patient_name'),
            "facility_name": patient_data.get('facility_name'),
            "phone_number": phone_number,
            "room_name": room_name,
            "dialout_id": dialout_data.get("id") if dialout_data else None,
            "message": f"Outbound call initiated to {phone_number} for {patient_data.get('patient_name')}"
        }
        
    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        logger.error("Daily API request timeout")
        raise HTTPException(status_code=504, detail="Daily.co API timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"Daily API request failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to communicate with Daily.co API")
    except Exception as e:
        logger.error(f"Error starting call: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to start call")

@app.get("/patients")
async def get_patients(skip: int = 0, limit: int = 50):
    """Get list of patients available for calling with pagination"""
    try:
        # Get patients with pending authorization
        pending_patients = await patient_db.find_patients_pending_auth()
        
        # Apply pagination
        total_count = len(pending_patients)
        paginated_patients = pending_patients[skip:skip+limit]
        
        patients = []
        for patient in paginated_patients:
            patients.append({
                "patient_id": str(patient["_id"]),
                "patient_name": patient.get("patient_name"),
                "facility_name": patient.get("facility_name"),
                "insurance_company_name": patient.get("insurance_company_name"),
                "prior_auth_status": patient.get("prior_auth_status"),
                "appointment_time": patient.get("appointment_time"),
                "call_status": patient.get("call_status", "Not Started"),
                "insurance_phone_number": patient.get("insurance_phone_number")
            })
        
        return {
            "patients": patients,
            "total_count": total_count,
            "skip": skip,
            "limit": limit
        }
        
    except Exception as e:
        logger.error(f"Error getting patients: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to load patients")

@app.get("/patients/{patient_id}")
async def get_patient(patient_id: str):
    """Get detailed information for a specific patient"""
    try:
        patient = await patient_db.find_patient_by_id(patient_id)
        
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        # Convert ObjectId to string
        patient['_id'] = str(patient['_id'])
        
        return {
            "status": "success",
            "patient": patient
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting patient: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to load patient details")

@app.get("/test-flow/{session_id}")
async def test_flow_state(session_id: str):
    """Debug endpoint to check flow state"""
    if session_id not in active_pipelines:
        raise HTTPException(status_code=404, detail="Session not found")
    
    pipeline = active_pipelines[session_id]
    if pipeline.flow_manager:
        return {
            "current_node": pipeline.flow_manager.state.get("current_node"),
            "collected_info": pipeline.flow_manager.state.get("collected_info"),
            "patient_id": pipeline.patient_id,
            "transcripts_count": len(pipeline.transcripts)
        }
    return {"error": "No flow manager"}

@app.get("/conversation-state/{session_id}")
async def get_conversation_state(session_id: str):
    """Get current conversation state"""
    try:
        if session_id not in active_pipelines:
            raise HTTPException(status_code=404, detail="Session not found")
        
        pipeline = active_pipelines[session_id]
        state = pipeline.get_conversation_state()
        
        return {
            "session_id": session_id,
            "patient_id": pipeline.patient_id,
            "workflow_state": state["workflow_state"],
            "patient_data": state["patient_data"],
            "collected_info": state["collected_info"]
        }
        
    except Exception as e:
        logger.error(f"Error getting conversation state: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to get conversation state")

@app.post("/end-call/{session_id}")
async def end_call(session_id: str):
    """End a call session and cleanup"""
    try:
        if session_id in active_pipelines:
            pipeline = active_pipelines[session_id]
            final_state = pipeline.get_conversation_state()
            patient_id = pipeline.patient_id
            
            # Save transcript to database
            transcript_json = json.dumps(pipeline.transcripts)
            await patient_db.update_call_info(
                patient_id=patient_id,
                call_status="Completed",
                call_transcript=transcript_json
            )
            
            logger.info(f"Saved transcript with {len(pipeline.transcripts)} messages for patient {patient_id}")
            
            # Cleanup
            del active_pipelines[session_id]
            
            logger.info(f"Ended healthcare call session: {session_id}")
            return {
                "status": "success", 
                "session_id": session_id,
                "patient_id": patient_id,
                "final_state": final_state["workflow_state"],
                "transcript_messages": len(pipeline.transcripts)
            }
        else:
            raise HTTPException(status_code=404, detail="Session not found")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ending call: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to end call")

@app.get("/active-sessions")
async def get_active_sessions():
    """Get list of active call sessions"""
    sessions = []
    
    for session_id, pipeline in active_pipelines.items():
        state = pipeline.get_conversation_state()
        
        sessions.append({
            "session_id": session_id,
            "patient_id": pipeline.patient_id,
            "workflow_state": state["workflow_state"],
            "has_patient_data": state["patient_data"] is not None
        })
    
    return {
        "active_sessions": sessions,
        "session_count": len(active_pipelines)
    }

@app.get("/health")
async def health_check():
    """Health check endpoint with database connectivity"""
    try:
        db_status = "connected"
        patient_count = len(await patient_db.find_patients_pending_auth())
    except Exception as e:
        db_status = f"error: {str(e)}"
        patient_count = 0
    
    return {
        "status": "healthy",
        "service": "healthcare-ai-agent",
        "active_sessions": len(active_pipelines),
        "database_status": db_status,
        "pending_patients": patient_count
    }

@app.get("/")
async def root():
    """Serve the main application interface"""
    try:
        return FileResponse("static/index.html")
    except FileNotFoundError:
        return HTMLResponse("""
        <html>
            <body style="font-family: Arial; padding: 40px; text-align: center;">
                <h1>Prior Authorization Voice Agent</h1>
                <p>Application is loading...</p>
                <p>If this persists, please contact support.</p>
            </body>
        </html>
        """)

if __name__ == "__main__":
    # Validate required environment variables
    required_vars = [
        "OPENAI_API_KEY", 
        "DEEPGRAM_API_KEY", 
        "DAILY_API_KEY",
        "DAILY_PHONE_NUMBER_ID",
        "MONGO_URI"
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        exit(1)
    
    logger.info("Starting Healthcare AI Agent server with Daily.co telephony...")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))