import os
import logging
import traceback
import asyncio
import datetime
import json
import time
import aiohttp
import yaml
import requests
import uvicorn
import uuid
import base64
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from bson import ObjectId
from pathlib import Path
from pipecat.utils.tracing.setup import setup_tracing
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# Engine imports
from core import ConversationSchema, DataFormatter, PromptRenderer
from core.client_loader import ClientLoader
from backend.models import get_async_patient_db, get_async_user_db
from backend.audit import get_audit_logger
from pipeline.runner import ConversationPipeline
from utils.validator import validate_patient_data
from jose import JWTError, jwt
from datetime import timedelta
from fastapi import Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# FASTAPI APP INITIALIZATION
# ============================================================================

app = FastAPI(title="Healthcare AI Agent", version="1.0.0")

# Configure CORS - restrict to specific origins for HIPAA compliance
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
logger.info(f"CORS allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,  # Cache preflight for 10 minutes
)

# Add security headers middleware for HIPAA compliance
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)

    # HSTS - Force HTTPS for 1 year
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"

    # XSS Protection (legacy browsers)
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Content Security Policy - restrict resource loading
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"

    # Referrer Policy - don't leak PHI in referrer
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Permissions Policy - restrict browser features
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    return response

if os.path.exists("frontend/build/assets"):
    app.mount("/assets", StaticFiles(directory="frontend/build/assets"), name="assets")
    logger.info("✅ Mounted /assets from frontend/build/assets")

# Serve other static files from build root (manifest.json, robots.txt, etc.)
if os.path.exists("frontend/build"):
    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse("frontend/build/manifest.json")
    
    @app.get("/robots.txt")
    async def serve_robots():
        return FileResponse("frontend/build/robots.txt")

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class CallRequest(BaseModel):
    patient_id: str
    client_name: str = "prior_auth"  # Default to prior_auth for now
    phone_number: Optional[str] = None

class CallResponse(BaseModel):
    status: str
    session_id: str
    room_name: str
    room_url: str
    message: str

class BulkPatientRequest(BaseModel):
    patients: list[dict]

class SignupRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class AuthResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    email: str

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def convert_objectid(doc: dict) -> dict:
    if doc and "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
        doc["patient_id"] = doc["_id"]
    return doc

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Security scheme for API authentication
security = HTTPBearer()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_client_info(request: Request) -> tuple[str, str]:
    """Extract client IP and User-Agent from request"""
    # Get IP address (handle proxies and X-Forwarded-For header)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else "unknown"

    # Get user agent
    user_agent = request.headers.get("user-agent", "unknown")

    return ip_address, user_agent

async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Validate JWT token and return user payload.
    Logs all API access for HIPAA compliance.

    Raises:
        HTTPException: 401 if token is invalid or expired
    """
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        email = payload.get("email")

        if not user_id or not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Log API access for audit trail
        ip_address, user_agent = get_client_info(request)
        await audit_logger.log_api_access(
            user_id=user_id,
            email=email,
            endpoint=request.url.path,
            method=request.method,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True
        )

        return payload

    except JWTError as e:
        logger.warning(f"JWT validation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def log_phi_access_wrapper(
    request: Request,
    user: dict,
    action: str,
    resource_type: str,
    resource_id: str
):
    """Helper to log PHI access with request context"""
    ip_address, user_agent = get_client_info(request)
    await audit_logger.log_phi_access(
        user_id=user["sub"],
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        user_agent=user_agent,
        endpoint=request.url.path
    )

@app.on_event("startup")
async def initialize_application():
    """Initialize application-level resources."""
    logger.info("🚀 Application starting...")

    # Initialize audit log indexes for HIPAA compliance
    try:
        await audit_logger.ensure_indexes()
        logger.info("✅ Audit log indexes initialized")
    except Exception as e:
        logger.warning(f"⚠️  Failed to create audit indexes: {e}")

    # Encode credentials for Basic Auth
    public_key = os.getenv('LANGFUSE_PUBLIC_KEY')
    secret_key = os.getenv('LANGFUSE_SECRET_KEY')
    auth_string = f"{public_key}:{secret_key}"
    encoded_auth = base64.b64encode(auth_string.encode()).decode()

    # Configure Langfuse OTLP exporter
    langfuse_exporter = OTLPSpanExporter(
        endpoint=f"{os.getenv('LANGFUSE_HOST')}/api/public/otel/v1/traces",
        headers={
            "Authorization": f"Basic {encoded_auth}"
        }
    )

    # Initialize Pipecat's tracing with Langfuse
    console_export = os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true"
    setup_tracing(
        service_name="voice-ai-pipeline",
        exporter=langfuse_exporter,
        console_export=console_export
    )

    logger.info("✅ OpenTelemetry tracing configured with Langfuse")
    logger.info("✅ Application ready")

# ============================================================================
# GLOBAL INSTANCES
# ============================================================================

active_pipelines = {}
patient_db = get_async_patient_db()
user_db = get_async_user_db()
audit_logger = get_audit_logger()

# ============================================================================
# CALL MANAGEMENT ENDPOINTS
# ============================================================================

@app.post("/start-call")
async def start_call(
    call_request: CallRequest,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Initiate a new call session (Protected - requires authentication)"""
    logger.info("=== INITIATING CALL ===")
    logger.info(f"Patient ID: {call_request.patient_id}")
    logger.info(f"User: {current_user['email']}")

    try:
        # Fetch patient data from database
        logger.info("Fetching patient data from database...")
        patient = await patient_db.find_patient_by_id(call_request.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        # Convert ObjectId to string if needed
        patient = convert_objectid(patient)
        
        logger.info(f"Patient found: {patient.get('name', 'Unnamed')}")

        # Log PHI access for starting call
        await log_phi_access_wrapper(
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
        room_name = f"call_{session_id}"
        
        logger.info(f"Session ID: {session_id}")
        logger.info(f"Room name: {room_name}")
        logger.info(f"Phone number: {phone_number}")
        
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
            raise HTTPException(status_code=500, detail="Failed to create Daily room")
        
        room_data = room_response.json()
        room_url = room_data["url"]
        logger.info(f"Room created: {room_url}")
        
        # Create meeting token with owner privileges
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
            raise HTTPException(status_code=500, detail="Failed to create meeting token")
        
        token = token_response.json()["token"]
        logger.info("Meeting token created with owner privileges")
        
        # Create conversation pipeline (NEW - client-agnostic)
        logger.info("Creating conversation pipeline...")
        pipeline = ConversationPipeline(
            client_name=call_request.client_name,
            session_id=session_id,
            patient_id=call_request.patient_id,
            patient_data=patient,
            phone_number=phone_number,
            debug_mode=os.getenv("DEBUG", "false").lower() == "true"
        )
        
        # Store in active pipelines
        active_pipelines[session_id] = pipeline
        logger.info(f"Pipeline stored (Total active: {len(active_pipelines)})")
        
        # Start the pipeline in background
        logger.info("Starting pipeline task...")
        asyncio.create_task(pipeline.run(room_url, token, room_name))
        
        return CallResponse(
            status="initiated",
            session_id=session_id,
            room_name=room_name,
            room_url=room_url,
            message="Call session initiated successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating call: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/call/{session_id}/status")
async def get_call_status(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get status of an active call (Protected - requires authentication)"""
    if session_id not in active_pipelines:
        raise HTTPException(status_code=404, detail="Call session not found")

    pipeline = active_pipelines[session_id]
    state = pipeline.get_conversation_state()

    # Log PHI access
    await log_phi_access_wrapper(
        request=request,
        user=current_user,
        action="view_status",
        resource_type="call",
        resource_id=session_id
    )

    return {
        "session_id": session_id,
        "status": state["workflow_state"],
        "current_state": state.get("current_state"),
        "state_history": state.get("state_history", []),
        "transcript_count": len(state.get("transcripts", []))
    }

@app.get("/call/{session_id}/transcript")
async def get_call_transcript(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get full transcript of a call (Protected - requires authentication)"""
    if session_id not in active_pipelines:
        raise HTTPException(status_code=404, detail="Call session not found")

    pipeline = active_pipelines[session_id]
    state = pipeline.get_conversation_state()

    # Log PHI access for transcript
    await log_phi_access_wrapper(
        request=request,
        user=current_user,
        action="view_transcript",
        resource_type="transcript",
        resource_id=session_id
    )

    return {
        "session_id": session_id,
        "transcripts": state.get("transcripts", []),
        "patient": state["patient_data"].get("patient_name")
    }

@app.delete("/call/{session_id}")
async def end_call(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """End an active call (Protected - requires authentication)"""
    if session_id not in active_pipelines:
        raise HTTPException(status_code=404, detail="Call session not found")

    pipeline = active_pipelines[session_id]

    # Log PHI access
    await log_phi_access_wrapper(
        request=request,
        user=current_user,
        action="end_call",
        resource_type="call",
        resource_id=session_id
    )

    # TODO: Add pipeline cleanup/termination logic
    logger.info(f"User {current_user['email']} ending call session: {session_id}")

    # Remove from active pipelines
    del active_pipelines[session_id]

    return {
        "status": "ended",
        "session_id": session_id,
        "message": "Call ended successfully"
    }

@app.get("/calls/active")
async def list_active_calls(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """List all active call sessions (Protected - requires authentication)"""
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

    # Log PHI access
    ip_address, user_agent = get_client_info(request)
    await audit_logger.log_phi_access(
        user_id=current_user["sub"],
        action="view_list",
        resource_type="call",
        resource_id="all",
        ip_address=ip_address,
        user_agent=user_agent,
        endpoint=request.url.path,
        details={"count": len(active_calls)}
    )

    return {
        "active_call_count": len(active_calls),
        "calls": active_calls
    }

# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

@app.post("/auth/signup", response_model=AuthResponse)
async def signup(request: Request, signup_data: SignupRequest):
    """Create a new user account with HIPAA-compliant password requirements"""
    try:
        # Get client info for audit logging
        ip_address, user_agent = get_client_info(request)

        # Create user
        user_id = await user_db.create_user(
            email=signup_data.email,
            password=signup_data.password,
            created_by=None,  # Self-registration
            role="user"
        )

        if not user_id:
            # Log failed signup attempt
            await audit_logger.log_event(
                event_type="signup",
                user_id=None,
                email=signup_data.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"error": "User creation failed"}
            )
            raise HTTPException(status_code=500, detail="Failed to create user account")

        # Log successful signup
        await audit_logger.log_event(
            event_type="signup",
            user_id=user_id,
            email=signup_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"role": "user"}
        )

        # Create access token
        access_token = create_access_token(
            data={"sub": user_id, "email": signup_data.email, "role": "user"}
        )

        logger.info(f"New user signed up: {signup_data.email} (ID: {user_id})")

        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            user_id=user_id,
            email=signup_data.email
        )

    except ValueError as e:
        # Password complexity or duplicate email error
        ip_address, user_agent = get_client_info(request)
        await audit_logger.log_event(
            event_type="signup",
            user_id=None,
            email=signup_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            details={"error": str(e)}
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error during signup: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/auth/login", response_model=AuthResponse)
async def login(request: Request, login_data: LoginRequest):
    """Authenticate user and return JWT token"""
    try:
        # Get client info for audit logging
        ip_address, user_agent = get_client_info(request)

        # Verify password
        is_valid, user = await user_db.verify_password(
            email=login_data.email,
            password=login_data.password
        )

        if not is_valid or not user:
            # Log failed login attempt
            await audit_logger.log_event(
                event_type="login",
                user_id=None,
                email=login_data.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Invalid credentials"}
            )

            # Check if account is locked
            failed_attempts = await audit_logger.get_failed_login_attempts(
                email=login_data.email,
                time_window_minutes=30
            )

            if failed_attempts >= 5:
                raise HTTPException(
                    status_code=403,
                    detail="Account locked due to too many failed login attempts. Please contact support."
                )

            raise HTTPException(
                status_code=401,
                detail="Invalid email or password"
            )

        # Check account status
        if user.get("status") == "locked":
            await audit_logger.log_event(
                event_type="login",
                user_id=str(user["_id"]),
                email=login_data.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Account locked"}
            )
            raise HTTPException(
                status_code=403,
                detail="Account is locked. Please contact support."
            )

        if user.get("status") == "inactive":
            await audit_logger.log_event(
                event_type="login",
                user_id=str(user["_id"]),
                email=login_data.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Account inactive"}
            )
            raise HTTPException(
                status_code=403,
                detail="Account is inactive. Please contact support."
            )

        # Log successful login
        user_id = str(user["_id"])
        await audit_logger.log_event(
            event_type="login",
            user_id=user_id,
            email=login_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"role": user.get("role", "user")}
        )

        # Create access token
        access_token = create_access_token(
            data={
                "sub": user_id,
                "email": login_data.email,
                "role": user.get("role", "user")
            }
        )

        logger.info(f"User logged in: {login_data.email} (ID: {user_id})")

        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            user_id=user_id,
            email=login_data.email
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during login: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/auth/logout")
async def logout(request: Request):
    """Log user logout event (client-side token removal)"""
    try:
        # Get client info
        ip_address, user_agent = get_client_info(request)

        # Extract user info from Authorization header if present
        auth_header = request.headers.get("authorization", "")
        user_email = "unknown"
        user_id = None

        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                user_id = payload.get("sub")
                user_email = payload.get("email", "unknown")
            except JWTError:
                pass

        # Log logout event
        await audit_logger.log_event(
            event_type="logout",
            user_id=user_id,
            email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={}
        )

        logger.info(f"User logged out: {user_email}")

        return {"message": "Logged out successfully"}

    except Exception as e:
        logger.error(f"Error during logout: {str(e)}")
        return {"message": "Logged out"}

# ============================================================================
# PATIENT MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/patients")
async def list_patients(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get all patients (Protected - requires authentication)"""
    try:
        logger.info(f"📋 User {current_user['email']} fetching all patients")

        # Sort by created_at descending (newest first)
        cursor = patient_db.patients.find().sort("created_at", -1)
        all_patients = await cursor.to_list(length=None)

        logger.info(f"🔍 Found {len(all_patients)} patients in database")

        patients = [convert_objectid(p) for p in all_patients]

        # Log PHI access (bulk view)
        ip_address, user_agent = get_client_info(request)
        await audit_logger.log_phi_access(
            user_id=current_user["sub"],
            action="view_list",
            resource_type="patient",
            resource_id="all",
            ip_address=ip_address,
            user_agent=user_agent,
            endpoint=request.url.path,
            details={"count": len(patients)}
        )

        logger.info(f"✅ Returning all {len(patients)} patients")

        return {
            "patients": patients,
            "total_count": len(patients)
        }

    except Exception as e:
        logger.error(f"❌ Error fetching patients: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/patients/{patient_id}")
async def get_patient_by_id(
    patient_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific patient by ID (Protected - requires authentication)"""
    try:
        patient = await patient_db.find_patient_by_id(patient_id)

        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Log PHI access
        await log_phi_access_wrapper(
            request=request,
            user=current_user,
            action="view",
            resource_type="patient",
            resource_id=patient_id
        )

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
async def add_patient(
    patient_data: dict,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Add a new patient (Protected - requires authentication)"""
    try:
        # Set defaults
        if "call_status" not in patient_data:
            patient_data["call_status"] = "Not Started"

        if "prior_auth_status" not in patient_data:
            patient_data["prior_auth_status"] = "Pending"

        patient_id = await patient_db.add_patient(patient_data)

        if not patient_id:
            raise HTTPException(status_code=500, detail="Failed to add patient")

        # Log PHI access
        await log_phi_access_wrapper(
            request=request,
            user=current_user,
            action="create",
            resource_type="patient",
            resource_id=patient_id
        )

        logger.info(f"User {current_user['email']} added new patient: {patient_data.get('patient_name')} (ID: {patient_id})")

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

@app.post("/add-patients-bulk")
async def add_patients_bulk(
    bulk_request: BulkPatientRequest,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Add multiple patients from CSV upload (Protected - requires authentication)"""
    try:
        success_count = 0
        failed_count = 0
        errors = []
        created_ids = []

        for idx, patient_data in enumerate(bulk_request.patients):
            # Validate patient data
            is_valid, error_msg = validate_patient_data(patient_data)

            if not is_valid:
                failed_count += 1
                errors.append({
                    "row": idx + 1,
                    "patient_name": patient_data.get("patient_name", "Unknown"),
                    "error": error_msg
                })
                continue

            # Set defaults
            if "call_status" not in patient_data:
                patient_data["call_status"] = "Not Started"

            if "prior_auth_status" not in patient_data:
                patient_data["prior_auth_status"] = "Pending"

            # Add patient to database
            patient_id = await patient_db.add_patient(patient_data)

            if patient_id:
                success_count += 1
                created_ids.append(patient_id)
                logger.info(f"Added patient: {patient_data.get('patient_name')} (ID: {patient_id})")
            else:
                failed_count += 1
                errors.append({
                    "row": idx + 1,
                    "patient_name": patient_data.get("patient_name", "Unknown"),
                    "error": "Database insertion failed"
                })

        # Log bulk PHI creation
        ip_address, user_agent = get_client_info(request)
        await audit_logger.log_phi_access(
            user_id=current_user["sub"],
            action="create_bulk",
            resource_type="patient",
            resource_id="bulk",
            ip_address=ip_address,
            user_agent=user_agent,
            endpoint=request.url.path,
            details={"success_count": success_count, "failed_count": failed_count}
        )

        return {
            "status": "completed",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None,
            "message": f"Successfully added {success_count} patients. {failed_count} failed."
        }

    except Exception as e:
        logger.error(f"Error in bulk patient upload: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/patients/{patient_id}")
async def update_patient(
    patient_id: str,
    patient_data: dict,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Update an existing patient (Protected - requires authentication)"""
    try:
        success = await patient_db.update_patient(patient_id, patient_data)
        if not success:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

        # Log PHI access
        await log_phi_access_wrapper(
            request=request,
            user=current_user,
            action="update",
            resource_type="patient",
            resource_id=patient_id
        )

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
async def delete_patient(
    patient_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Delete a patient (Protected - requires authentication)"""
    try:
        success = await patient_db.delete_patient(patient_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

        # Log PHI access
        await log_phi_access_wrapper(
            request=request,
            user=current_user,
            action="delete",
            resource_type="patient",
            resource_id=patient_id
        )

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
            "templates_cached": len(prompt_renderer._cache),
            "init_time_ok": startup_metrics["total_init_ms"] < 500
        }
    }

# ============================================================================
# MONITORING API ENDPOINTS
# ============================================================================



# ============================================================================
# HEALTH & STATUS ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check with schema system status"""
    try:
        db_status = "connected"
        patient_count = len(await patient_db.find_patients_by_status("Pending"))
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
            "templates_cached": len(prompt_renderer._cache) if prompt_renderer else 0
        }
    }

# ============================================================================
# SPA CATCH-ALL ROUTE
# ============================================================================

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """
    Catch-all to serve React SPA. Explicitly excludes /assets to let mount handle it.
    """
    # Don't catch /assets/* - let the StaticFiles mount handle it
    if full_path.startswith("assets"):
        raise HTTPException(status_code=404)
    
    # Serve index.html for all other routes (React Router handles routing)
    index_path = "frontend/build/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    else:
        raise HTTPException(
            status_code=503,
            detail="Frontend not built. Run: cd frontend && npm run build"
        )

# ============================================================================
# DEVELOPMENT SERVER
# ============================================================================

def validate_environment_variables():
    """
    Validate required environment variables and their security requirements.
    Returns list of validation errors.
    """
    errors = []

    # Required variables
    required_vars = {
        "OPENAI_API_KEY": lambda x: x.startswith("sk-"),
        "DEEPGRAM_API_KEY": lambda x: len(x) > 20,
        "DAILY_API_KEY": lambda x: len(x) > 20,
        "DAILY_PHONE_NUMBER_ID": lambda x: len(x) > 10,
        "MONGO_URI": lambda x: x.startswith("mongodb"),
        "JWT_SECRET_KEY": lambda x: len(x) >= 32,
    }

    for var, validator in required_vars.items():
        value = os.getenv(var)
        if not value:
            errors.append(f"❌ Missing required variable: {var}")
        elif not validator(value):
            errors.append(f"❌ Invalid format for: {var}")

    # Security checks
    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    if jwt_secret == "your-secret-key-change-in-production":
        errors.append("❌ CRITICAL: JWT_SECRET_KEY must be changed from default!")

    allowed_origins = os.getenv("ALLOWED_ORIGINS", "*")
    if "*" in allowed_origins:
        errors.append("⚠️  WARNING: ALLOWED_ORIGINS contains '*' - this is insecure for production")

    # Optional but recommended
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        errors.append("⚠️  WARNING: LANGFUSE_PUBLIC_KEY not set - observability disabled")

    return errors

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Healthcare AI Agent - HIPAA Compliance Mode")
    logger.info("=" * 60)

    # Validate environment variables
    validation_errors = validate_environment_variables()

    if validation_errors:
        logger.error("Environment validation failed:")
        for error in validation_errors:
            logger.error(f"  {error}")

        # Exit only on critical errors (not warnings)
        critical_errors = [e for e in validation_errors if e.startswith("❌")]
        if critical_errors:
            logger.error("\n❌ Cannot start application due to critical configuration errors")
            exit(1)
        else:
            logger.warning("\n⚠️  Starting with warnings - address these before production deployment")

    logger.info("✅ Environment validation passed")
    logger.info("Starting Healthcare AI Agent server with Daily.co telephony...")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))