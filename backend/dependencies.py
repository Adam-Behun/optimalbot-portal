"""Dependency injection providers for FastAPI"""
import os
import logging
from typing import Tuple
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from backend.models import AsyncPatientRecord, AsyncUserRecord, get_async_patient_db, get_async_user_db
from backend.sessions import AsyncSessionRecord, get_async_session_db
from backend.audit import AuditLogger, get_audit_logger

logger = logging.getLogger(__name__)

security = HTTPBearer()
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ALGORITHM = "HS256"


# Database dependencies
def get_patient_db() -> AsyncPatientRecord:
    return get_async_patient_db()

def get_user_db() -> AsyncUserRecord:
    return get_async_user_db()

def get_session_db() -> AsyncSessionRecord:
    return get_async_session_db()

def get_audit_logger_dep() -> AuditLogger:
    return get_audit_logger()


# Utilities
def get_client_info(request: Request) -> Tuple[str, str]:
    """Extract IP and User-Agent from request"""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else "unknown"

    user_agent = request.headers.get("user-agent", "unknown")
    return ip_address, user_agent


async def log_phi_access(
    request: Request,
    user: dict,
    action: str,
    resource_type: str,
    resource_id: str
):
    """Log PHI access for HIPAA compliance"""
    audit_logger = get_audit_logger()
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


# Authentication
async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
) -> dict:
    """Validate JWT and log API access"""
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

        # Log API access
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


# Rate limiting
def get_user_id_from_request(request: Request) -> str:
    """Extract user ID from JWT for rate limiting, fallback to IP"""
    try:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return f"user:{payload.get('sub')}"
    except:
        pass

    ip_address = get_client_info(request)[0]
    return f"ip:{ip_address}"
