"""Authentication endpoints"""
import os
import logging
import traceback
from datetime import timedelta, datetime
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from jose import JWTError, jwt
from slowapi import Limiter

from backend.dependencies import get_user_db, get_audit_logger_dep, get_client_info, get_user_id_from_request
from backend.models import AsyncUserRecord
from backend.audit import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_user_id_from_request)

# JWT config
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# Models
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

class RequestResetRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    email: str
    token: str
    new_password: str

class ResetTokenResponse(BaseModel):
    message: str
    token: str
    expires_in_minutes: int


# Helper
def create_access_token(data: dict, expires_delta: timedelta = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


@router.post("/signup", response_model=AuthResponse)
async def signup(
    request: Request,
    signup_data: SignupRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Create new user account"""
    try:
        ip_address, user_agent = get_client_info(request)

        user_id = await user_db.create_user(
            email=signup_data.email,
            password=signup_data.password,
            created_by=None,
            role="user"
        )

        if not user_id:
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

        await audit_logger.log_event(
            event_type="signup",
            user_id=user_id,
            email=signup_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"role": "user"}
        )

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


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    login_data: LoginRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Authenticate user and return JWT token"""
    try:
        ip_address, user_agent = get_client_info(request)

        is_valid, user = await user_db.verify_password(
            email=login_data.email,
            password=login_data.password
        )

        if not is_valid or not user:
            await audit_logger.log_event(
                event_type="login",
                user_id=None,
                email=login_data.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Invalid credentials"}
            )

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


@router.post("/logout")
async def logout(
    request: Request,
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Log user logout event"""
    try:
        ip_address, user_agent = get_client_info(request)

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


@router.post("/request-reset", response_model=ResetTokenResponse)
@limiter.limit("3/hour")
async def request_reset(
    request: Request,
    reset_request: RequestResetRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Generate password reset token (Level 1: token returned directly)"""
    try:
        ip_address, user_agent = get_client_info(request)

        success, token = await user_db.generate_reset_token(reset_request.email)

        if not success or not token:
            await audit_logger.log_event(
                event_type="password_reset_request",
                user_id=None,
                email=reset_request.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"error": "User not found"}
            )
            raise HTTPException(status_code=404, detail="Email not found")

        await audit_logger.log_event(
            event_type="password_reset_request",
            user_id=None,
            email=reset_request.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={}
        )

        logger.info(f"Password reset requested for {reset_request.email}")

        return ResetTokenResponse(
            message="Reset token generated. Use this token to reset your password.",
            token=token,
            expires_in_minutes=60
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during password reset request: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/reset-password")
@limiter.limit("5/hour")
async def reset_password(
    request: Request,
    reset_data: ResetPasswordRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Reset password using token"""
    try:
        ip_address, user_agent = get_client_info(request)

        success, error = await user_db.reset_password_with_token(
            email=reset_data.email,
            token=reset_data.token,
            new_password=reset_data.new_password
        )

        if not success:
            await audit_logger.log_event(
                event_type="password_reset",
                user_id=None,
                email=reset_data.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"error": error}
            )
            raise HTTPException(status_code=400, detail=error)

        await audit_logger.log_event(
            event_type="password_reset",
            user_id=None,
            email=reset_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={}
        )

        logger.info(f"Password reset completed for {reset_data.email}")

        return {"message": "Password reset successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during password reset: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
