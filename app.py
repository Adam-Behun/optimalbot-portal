import os
import logging
import traceback
import asyncio
import datetime
import uvicorn
import uuid
import base64
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from bson import ObjectId
from pipecat.utils.tracing.setup import setup_tracing
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from pipecatcloud.session import Session, SessionParams
from pipecatcloud.exception import AgentStartError

# Backend imports
from backend.models import get_async_patient_db, get_async_user_db
from backend.audit import get_audit_logger
from backend.sessions import get_async_session_db
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

# Configure CORS - Vercel frontend + local development
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://your-app.vercel.app,http://localhost:3000"
).split(",")
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

patient_db = get_async_patient_db()
user_db = get_async_user_db()
session_db = get_async_session_db()
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

        logger.info(f"Session ID: {session_id}")
        logger.info(f"Phone number: {phone_number}")

        # Create session record in MongoDB
        await session_db.create_session({
            "session_id": session_id,
            "patient_id": call_request.patient_id,
            "phone_number": phone_number,
            "client_name": call_request.client_name
        })

        # Start Pipecat Cloud bot session
        logger.info("Starting Pipecat Cloud bot session...")

        try:
            # Get agent name and API key from environment
            agent_name = os.getenv("PIPECAT_AGENT_NAME", "healthcare-voice-ai")
            pipecat_api_key = os.getenv("PIPECAT_API_KEY")

            if not pipecat_api_key:
                raise HTTPException(status_code=500, detail="PIPECAT_API_KEY not configured")

            # Create session with Pipecat Cloud
            session = Session(
                agent_name=agent_name,
                api_key=pipecat_api_key,
                params=SessionParams(
                    use_daily=True,  # Pipecat Cloud creates Daily room
                    daily_room_properties={
                        "enable_dialout": True,
                        "enable_chat": False,
                        "enable_screenshare": False,
                        "enable_recording": "cloud",
                        "exp": int(datetime.datetime.now().timestamp()) + 3600
                    },
                    data={
                        "patient_id": call_request.patient_id,
                        "patient_data": patient,  # Pass as dict (not JSON string)
                        "phone_number": phone_number,
                        "client_name": call_request.client_name
                    }
                )
            )

            # Start bot (Pipecat Cloud handles everything)
            response = await session.start()

            room_url = response.get("dailyRoom")
            token = response.get("dailyToken")

            logger.info(f"✅ Bot started via Pipecat Cloud")
            logger.info(f"Room: {room_url}")

            # Update session with room info
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

@app.get("/call/{session_id}/status")
async def get_call_status(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get status of an active call (Protected - requires authentication)"""
    session = await session_db.find_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    # Get patient data for additional context
    patient = await patient_db.find_patient_by_id(session["patient_id"])

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
        "status": session.get("status"),
        "patient_name": patient.get("patient_name") if patient else None,
        "call_status": patient.get("call_status") if patient else None,
        "created_at": session.get("created_at"),
        "pid": session.get("pid")
    }

@app.get("/call/{session_id}/transcript")
async def get_call_transcript(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Get full transcript of a call (Protected - requires authentication)"""
    session = await session_db.find_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    # Get patient with transcript
    patient = await patient_db.find_patient_by_id(session["patient_id"])
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

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
        "transcripts": patient.get("call_transcript", {}).get("messages", []),
        "patient_name": patient.get("patient_name")
    }

@app.delete("/call/{session_id}")
async def end_call(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """End an active call (Protected - requires authentication)"""
    session = await session_db.find_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")

    # Log PHI access
    await log_phi_access_wrapper(
        request=request,
        user=current_user,
        action="end_call",
        resource_type="call",
        resource_id=session_id
    )

    logger.info(f"User {current_user['email']} ending call session: {session_id}")

    # Kill bot process if still running
    if "pid" in session:
        try:
            import signal
            os.kill(session["pid"], signal.SIGTERM)
            logger.info(f"Terminated bot process PID: {session['pid']}")
        except ProcessLookupError:
            logger.info(f"Bot process {session['pid']} already terminated")
        except Exception as e:
            logger.warning(f"Failed to kill process: {e}")

    # Mark session as completed
    await session_db.update_session(session_id, {"status": "terminated"})

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
    sessions = await session_db.list_active_sessions()

    # Enrich with patient data
    active_calls = []
    for session in sessions:
        patient = await patient_db.find_patient_by_id(session["patient_id"])
        active_calls.append({
            "session_id": session["session_id"],
            "patient_id": session["patient_id"],
            "patient_name": patient.get("patient_name") if patient else None,
            "status": session.get("status"),
            "created_at": session.get("created_at")
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
# HEALTH & STATUS ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check for bot runner API"""
    try:
        db_status = "connected"
        patient_count = len(await patient_db.find_patients_by_status("Pending"))
    except Exception as e:
        db_status = f"error: {str(e)}"
        patient_count = 0

    try:
        active_sessions = await session_db.list_active_sessions()
        session_count = len(active_sessions)
    except Exception:
        session_count = 0

    return {
        "status": "healthy",
        "service": "healthcare-ai-bot-runner",
        "active_sessions": session_count,
        "database": {
            "status": db_status,
            "pending_patients": patient_count
        }
    }

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