import os
import logging
import traceback
import asyncio
import datetime
import json
import re
import requests
import time
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn
import uuid
from typing import Optional, Dict, Any
from bson import ObjectId

# Engine imports
from engine import ConversationSchema, DataFormatter, PromptRenderer
from models import get_async_patient_db
from schema_pipeline import SchemaBasedPipeline

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# FASTAPI APP INITIALIZATION
# ============================================================================

app = FastAPI(title="Healthcare AI Agent", version="1.0.0")

app.mount("/static", StaticFiles(directory="frontend/build/static"), name="static")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class CallRequest(BaseModel):
    patient_id: str
    phone_number: Optional[str] = None

class CallResponse(BaseModel):
    status: str
    session_id: str
    room_name: str
    room_url: str
    message: str

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def convert_objectid(doc: dict) -> dict:
    if doc and "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])  # Convert _id in place to string
        doc["patient_id"] = doc["_id"]  # Set patient_id as a copy
    return doc

# ============================================================================
# GLOBAL SCHEMA SYSTEM (Loaded once at startup, shared across all calls)
# ============================================================================

conversation_schema: ConversationSchema = None
prompt_renderer: PromptRenderer = None
data_formatter: DataFormatter = None

# Performance metrics
startup_metrics = {
    "schema_load_ms": 0.0,
    "renderer_init_ms": 0.0,
    "total_init_ms": 0.0
}

@app.on_event("startup")
async def initialize_schema_system():
    """
    Load and initialize the schema system once at startup.
    Everything is cached and reused across all calls.
    """
    global conversation_schema, prompt_renderer, data_formatter, startup_metrics
    
    schema_path = os.getenv("CONVERSATION_SCHEMA", "clients/prior_auth")
    
    logger.info(f"🔧 Initializing schema system from: {schema_path}")
    total_start = time.perf_counter()
    
    try:
        # 1. Load schema (includes validation)
        schema_start = time.perf_counter()
        conversation_schema = ConversationSchema.load(schema_path)
        startup_metrics["schema_load_ms"] = (time.perf_counter() - schema_start) * 1000
        
        # 2. Initialize prompt renderer (pre-compiles all templates)
        renderer_start = time.perf_counter()
        prompt_renderer = PromptRenderer(conversation_schema)
        startup_metrics["renderer_init_ms"] = (time.perf_counter() - renderer_start) * 1000
        
        # 3. Create data formatter (lightweight, no work until used)
        data_formatter = DataFormatter(conversation_schema)
        
        # Calculate total
        startup_metrics["total_init_ms"] = (time.perf_counter() - total_start) * 1000
        
        # Log success
        logger.info(
            f"✅ Schema system initialized:\n"
            f"   Name: {conversation_schema.conversation.name} v{conversation_schema.conversation.version}\n"
            f"   Schema load: {startup_metrics['schema_load_ms']:.1f}ms\n"
            f"   Template compile: {startup_metrics['renderer_init_ms']:.1f}ms\n"
            f"   Total: {startup_metrics['total_init_ms']:.1f}ms\n"
            f"   States: {len(conversation_schema.states.definitions)}\n"
            f"   Templates cached: {len(prompt_renderer._template_cache)}"
        )
        
        # Warn if slow
        if startup_metrics["total_init_ms"] > 500:
            logger.warning(
                f"⚠️  Schema init took {startup_metrics['total_init_ms']:.1f}ms (target: <500ms)"
            )
            
    except Exception as e:
        logger.error(f"❌ Schema system initialization failed: {e}")
        logger.error(traceback.format_exc())
        raise RuntimeError("Cannot start without schema system")

# ============================================================================
# GLOBAL INSTANCES
# ============================================================================

active_pipelines = {}
patient_db = get_async_patient_db()

# ============================================================================
# CALL MANAGEMENT ENDPOINTS
# ============================================================================

@app.post("/start-call", response_model=CallResponse)
async def initiate_call(request: CallRequest):
    """
    Initiate an outbound call using schema-driven pipeline.
    """
    logger.info(f"=== INITIATING CALL ===")
    logger.info(f"Patient ID: {request.patient_id}")
    
    try:
        # Fetch patient data from database
        logger.info("Fetching patient data from database...")
        patient_data = await patient_db.find_patient_by_id(request.patient_id)  # ← FIXED
        
        if not patient_data:
            raise HTTPException(status_code=404, detail=f"Patient {request.patient_id} not found")
        
        # Convert ObjectId to string
        patient_data = convert_objectid(patient_data)  # ← ADD THIS
        
        logger.info(f"Patient found: {patient_data.get('patient_name')}")
        
        # Use provided phone or get from patient data
        phone_number = request.phone_number or patient_data.get("insurance_phone_number") or patient_data.get("phone_number")
        if not phone_number:
            raise HTTPException(status_code=400, detail="No phone number provided or found in patient data")
        
        # Update patient call status to "In Progress"
        await patient_db.update_call_info(
            patient_id=request.patient_id,
            call_status="In Progress",
            insurance_phone_number=phone_number
        )
        
        # Generate session ID
        session_id = str(uuid.uuid4())
        room_name = f"call_{session_id}"
        
        logger.info(f"Session ID: {session_id}")
        logger.info(f"Room name: {room_name}")
        logger.info(f"Phone: {phone_number}")
        
        # Create Daily room
        logger.info("Creating Daily room...")
        daily_api_url = "https://api.daily.co/v1/rooms"
        daily_headers = {
            "Authorization": f"Bearer {os.getenv('DAILY_API_KEY')}",
            "Content-Type": "application/json"
        }
        
        room_response = requests.post(
            daily_api_url,
            headers=daily_headers,
            json={
                "name": room_name,
                "properties": {
                    "enable_dialout": True,
                    "enable_chat": False,
                    "enable_screenshare": False,
                    "enable_recording": "cloud",
                    "exp": int(datetime.datetime.now().timestamp()) + 3600
                }
            }
        )
        
        if room_response.status_code not in [200, 201]:
            logger.error(f"Failed to create room: {room_response.text}")
            await patient_db.update_call_info(request.patient_id, call_status="Not Started")
            raise HTTPException(status_code=500, detail="Failed to create Daily room")
        
        room_data = room_response.json()
        room_url = room_data["url"]
        logger.info(f"Room created: {room_url}")
        
        # Create meeting token
        logger.info("Creating meeting token...")
        token_response = requests.post(
            "https://api.daily.co/v1/meeting-tokens",
            headers=daily_headers,
            json={
                "properties": {
                    "room_name": room_name,
                    "is_owner": True
                }
            }
        )
        
        if token_response.status_code not in [200, 201]:
            logger.error(f"Failed to create token: {token_response.text}")
            await patient_db.update_call_info(request.patient_id, call_status="Not Started")
            raise HTTPException(status_code=500, detail="Failed to create meeting token")
        
        token = token_response.json()["token"]
        logger.info("Meeting token created")
        
        # Create schema-based pipeline
        logger.info("Creating schema-based pipeline...")
        pipeline = SchemaBasedPipeline(
            session_id=session_id,
            patient_id=request.patient_id,
            patient_data=patient_data,
            conversation_schema=conversation_schema,
            data_formatter=data_formatter,
            debug_mode=os.getenv("DEBUG", "false").lower() == "true"
        )
        
        # Store in active pipelines
        active_pipelines[session_id] = pipeline
        logger.info(f"Pipeline stored (Total active: {len(active_pipelines)})")
        
        # Start pipeline in background
        logger.info("Starting pipeline task...")
        asyncio.create_task(pipeline.run(room_url, token, room_name))
        
        # Wait for pipeline to initialize
        await asyncio.sleep(5)
        
        # Initiate dialout
        logger.info(f"Initiating dialout to {phone_number}...")
        dialout_response = requests.post(
            f"https://api.daily.co/v1/rooms/{room_name}/dialOut/start",
            headers=daily_headers,
            json={
                "phoneNumber": phone_number,
                "callerId": os.getenv("DAILY_PHONE_NUMBER_ID")
            }
        )
        
        if dialout_response.status_code not in [200, 201]:
            logger.error(f"Dialout failed: {dialout_response.text}")
            await patient_db.update_call_info(request.patient_id, call_status="Not Started")
            raise HTTPException(status_code=500, detail="Failed to initiate dialout")
        
        logger.info("✅ Dialout initiated successfully")
        
        return CallResponse(
            status="initiated",
            session_id=session_id,
            room_name=room_name,
            room_url=room_url,
            message=f"Call initiated to {phone_number} for patient {patient_data.get('patient_name')}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating call: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/call/{session_id}/status")
async def get_call_status(session_id: str):
    """Get status of an active call"""
    if session_id not in active_pipelines:
        raise HTTPException(status_code=404, detail="Call session not found")
    
    pipeline = active_pipelines[session_id]
    state = pipeline.get_conversation_state()
    
    return {
        "session_id": session_id,
        "status": state["workflow_state"],
        "current_state": state.get("current_state"),
        "state_history": state.get("state_history", []),
        "transcript_count": len(state.get("transcripts", []))
    }


@app.get("/call/{session_id}/transcript")
async def get_call_transcript(session_id: str):
    """Get full transcript of a call"""
    if session_id not in active_pipelines:
        raise HTTPException(status_code=404, detail="Call session not found")
    
    pipeline = active_pipelines[session_id]
    state = pipeline.get_conversation_state()
    
    return {
        "session_id": session_id,
        "transcripts": state.get("transcripts", []),
        "patient": state["patient_data"].get("patient_name")
    }


@app.delete("/call/{session_id}")
async def end_call(session_id: str):
    """End an active call"""
    if session_id not in active_pipelines:
        raise HTTPException(status_code=404, detail="Call session not found")
    
    pipeline = active_pipelines[session_id]
    
    # TODO: Add pipeline cleanup/termination logic
    logger.info(f"Ending call session: {session_id}")
    
    # Remove from active pipelines
    del active_pipelines[session_id]
    
    return {
        "status": "ended",
        "session_id": session_id,
        "message": "Call ended successfully"
    }


@app.get("/calls/active")
async def list_active_calls():
    """List all active call sessions"""
    active_calls = []
    
    for session_id, pipeline in active_pipelines.items():
        state = pipeline.get_conversation_state()
        active_calls.append({
            "session_id": session_id,
            "patient_id": pipeline.patient_id,
            "patient_name": state["patient_data"].get("patient_name"),
            "current_state": state.get("current_state"),
            "status": state["workflow_state"]
        })
    
    return {
        "active_call_count": len(active_calls),
        "calls": active_calls
    }

# ============================================================================
# PATIENT MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/patients")
async def list_patients(skip: int = 0, limit: int = 50):
    """Get all patients pending prior authorization"""
    try:
        pending_patients = await patient_db.find_patients_pending_auth()
        
        # Convert ObjectIds to strings
        patients = [convert_objectid(p) for p in pending_patients]
        
        # Apply pagination
        total_count = len(patients)
        paginated = patients[skip:skip+limit]
        
        return {
            "patients": paginated,
            "total_count": total_count,
            "skip": skip,
            "limit": limit
        }
        
    except Exception as e:
        logger.error(f"Error fetching patients: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/patients/{patient_id}")
async def get_patient_by_id(patient_id: str):
    """Get a specific patient by ID"""
    try:
        patient = await patient_db.find_patient_by_id(patient_id)
        
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        return {
            "status": "success",
            "patient": convert_objectid(patient)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching patient {patient_id}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/add-patient")
async def add_patient(patient_data: dict):
    """Add a new patient"""
    try:
        # Set defaults
        if "call_status" not in patient_data:
            patient_data["call_status"] = "Not Started"
        
        if "prior_auth_status" not in patient_data:
            patient_data["prior_auth_status"] = "Pending"
        
        patient_id = await patient_db.add_patient(patient_data)
        
        if not patient_id:
            raise HTTPException(status_code=500, detail="Failed to add patient")
        
        logger.info(f"Added new patient: {patient_data.get('patient_name')} (ID: {patient_id})")
        
        return {
            "status": "success",
            "patient_id": str(patient_id),
            "patient_name": patient_data.get('patient_name'),
            "message": f"Patient {patient_data.get('patient_name')} added successfully"
        }
        
    except Exception as e:
        logger.error(f"Error creating patient: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/patients/{patient_id}")
async def update_patient(patient_id: str, patient_data: dict):
    """Update an existing patient"""
    try:
        success = await patient_db.update_patient(patient_id, patient_data)
        if not success:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        return {
            "status": "success",
            "message": "Patient updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating patient {patient_id}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/patients/{patient_id}")
async def delete_patient(patient_id: str):
    """Delete a patient"""
    try:
        success = await patient_db.delete_patient(patient_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        return {
            "status": "success",
            "message": "Patient deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting patient {patient_id}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# SCHEMA MONITORING & TESTING ENDPOINTS
# ============================================================================

@app.get("/schema/info")
async def get_schema_info():
    """Schema metadata and performance metrics"""
    if not conversation_schema:
        raise HTTPException(status_code=503, detail="Schema not loaded")
    
    return {
        "schema": {
            "name": conversation_schema.conversation.name,
            "version": conversation_schema.conversation.version,
            "client_id": conversation_schema.conversation.client_id,
        },
        "states": {
            "initial": conversation_schema.states.initial_state,
            "total": len(conversation_schema.states.definitions),
            "names": [s.name for s in conversation_schema.states.definitions]
        },
        "voice": {
            "persona": conversation_schema.voice.persona.name,
            "role": conversation_schema.voice.persona.role,
            "tone": conversation_schema.voice.speaking_style.tone
        },
        "performance": startup_metrics,
        "health": {
            "schema_loaded": True,
            "templates_cached": len(prompt_renderer._template_cache),
            "init_time_ok": startup_metrics["total_init_ms"] < 500
        }
    }


@app.get("/schema/test-precompute")
async def test_precompute_performance():
    """Test data pre-computation performance"""
    if not data_formatter:
        raise HTTPException(status_code=503, detail="Formatter not initialized")
    
    # Sample patient data
    sample_data = {
        "patient_name": "John Smith",
        "date_of_birth": "1980-01-15",
        "insurance_member_id": "ABC123XYZ",
        "insurance_company_name": "Blue Cross Blue Shield",
        "cpt_code": "99213",
        "provider_npi": "1234567890"
    }
    
    # Measure pre-computation
    start = time.perf_counter()
    precomputed = data_formatter.format_patient_data(sample_data)
    compute_ms = (time.perf_counter() - start) * 1000
    
    return {
        "input": sample_data,
        "output": {
            k: v for k, v in precomputed.items() 
            if k.endswith("_spoken")
        },
        "performance": {
            "precompute_ms": round(compute_ms, 2),
            "target_ms": 50,
            "status": "✅ PASS" if compute_ms < 50 else "⚠️  SLOW"
        }
    }


@app.get("/schema/test-render/{state_name}")
async def test_render_performance(state_name: str):
    """Test prompt rendering performance for a specific state"""
    if not prompt_renderer:
        raise HTTPException(status_code=503, detail="Renderer not initialized")
    
    # Verify state exists
    try:
        conversation_schema.get_state(state_name)
    except ValueError:
        raise HTTPException(
            status_code=404, 
            detail=f"State '{state_name}' not found. Available: {[s.name for s in conversation_schema.states.definitions]}"
        )
    
    # Sample pre-computed data
    sample_data = {
        "patient_name": "John Smith",
        "date_of_birth_spoken": "January fifteenth, nineteen eighty",
        "insurance_member_id_spoken": "Alpha Bravo Charlie one two three X-ray Yankee Zulu",
        "insurance_company_name": "Blue Cross Blue Shield",
        "cpt_code_spoken": "9 9 2 1 3",
        "provider_npi_spoken": "123 456 7890",
        "_id": "test123"
    }
    
    # Measure rendering
    start = time.perf_counter()
    try:
        rendered = prompt_renderer.render_state_prompt(
            state_name, 
            sample_data,
            {"returning_from_hold": False}
        )
        render_ms = (time.perf_counter() - start) * 1000
        
        return {
            "state": state_name,
            "prompt_length": len(rendered),
            "prompt_preview": rendered[:500] + "..." if len(rendered) > 500 else rendered,
            "performance": {
                "render_ms": round(render_ms, 2),
                "target_ms": 10,
                "status": "✅ PASS" if render_ms < 10 else "⚠️  SLOW"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {str(e)}")

# ============================================================================
# HEALTH & STATUS ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check with schema system status"""
    try:
        db_status = "connected"
        patient_count = len(await patient_db.find_patients_pending_auth())
    except Exception as e:
        db_status = f"error: {str(e)}"
        patient_count = 0
    
    schema_health = "healthy" if conversation_schema and prompt_renderer else "not_initialized"
    
    return {
        "status": "healthy" if schema_health == "healthy" else "degraded",
        "service": "healthcare-ai-agent",
        "active_sessions": len(active_pipelines),
        "database": {
            "status": db_status,
            "pending_patients": patient_count
        },
        "schema_system": {
            "status": schema_health,
            "init_time_ms": startup_metrics.get("total_init_ms", 0),
            "templates_cached": len(prompt_renderer._template_cache) if prompt_renderer else 0
        }
    }

# ============================================================================
# FRONTEND SERVING
# ============================================================================

@app.get("/")
async def root():
    return FileResponse("frontend/build/index.html")

@app.get("/{full_path:path}")
async def serve_react_app(full_path: str):
    """Serve React app for all non-API routes"""
    return FileResponse("frontend/build/index.html")

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

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