import os
import secrets
from datetime import timedelta, datetime
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from jose import JWTError, jwt
from slowapi import Limiter
from loguru import logger

from backend.dependencies import get_user_db, get_audit_logger_dep, get_client_info, get_user_id_from_request, get_organization_db
from backend.models import AsyncUserRecord
from backend.models.organization import AsyncOrganizationRecord
from backend.audit import AuditLogger
router = APIRouter()
limiter = Limiter(key_func=get_user_id_from_request)

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


class LoginRequest(BaseModel):
    email: str
    password: str
    organization_slug: str | None = None

class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    branding: dict
    workflows: dict  # Contains workflow configs with patient_schema

class AuthResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    email: str
    organization: OrganizationResponse

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


class OrganizationSummary(BaseModel):
    id: str
    name: str
    slug: str


class CentralLoginResponse(BaseModel):
    user_id: str
    email: str
    organizations: list[OrganizationSummary]
    handoff_token: str
    handoff_expires_in: int


class ExchangeTokenRequest(BaseModel):
    token: str
    organization_slug: str


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    login_data: LoginRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
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
        organization_id = str(user.get("organization_id", ""))

        org = await org_db.get_by_id(organization_id)
        if not org:
            raise HTTPException(
                status_code=403,
                detail="User's organization not found"
            )

        if login_data.organization_slug:
            if org.get("slug") != login_data.organization_slug:
                await audit_logger.log_event(
                    event_type="login",
                    user_id=user_id,
                    email=login_data.email,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    success=False,
                    details={"reason": "User not authorized for this organization"}
                )
                raise HTTPException(
                    status_code=401,
                    detail="User not authorized for this organization"
                )

        organization_slug = org.get("slug", "")

        await audit_logger.log_event(
            event_type="login",
            user_id=user_id,
            email=login_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"role": user.get("role", "user")},
            organization_id=organization_id
        )

        access_token = create_access_token(
            data={
                "sub": user_id,
                "email": login_data.email,
                "role": user.get("role", "user"),
                "organization_id": organization_id,
                "organization_slug": organization_slug
            }
        )

        logger.info(f"User logged in: {login_data.email} (ID: {user_id}, Org: {organization_slug})")

        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            user_id=user_id,
            email=login_data.email,
            organization=OrganizationResponse(
                id=organization_id,
                name=org.get("name", ""),
                slug=organization_slug,
                branding=org.get("branding", {}),
                workflows=org.get("workflows", {})
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during login")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logout")
async def logout(
    request: Request,
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
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


@router.post("/login-central", response_model=CentralLoginResponse)
@limiter.limit("5/minute")
async def login_central(
    request: Request,
    login_data: LoginRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Central login for marketing site. Returns handoff token for redirect to tenant."""
    try:
        ip_address, user_agent = get_client_info(request)

        is_valid, user = await user_db.verify_password(
            email=login_data.email,
            password=login_data.password
        )

        if not is_valid or not user:
            await audit_logger.log_event(
                event_type="login_central",
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

            raise HTTPException(status_code=401, detail="Invalid email or password")

        if user.get("status") == "locked":
            raise HTTPException(status_code=403, detail="Account is locked. Please contact support.")

        if user.get("status") == "inactive":
            raise HTTPException(status_code=403, detail="Account is inactive. Please contact support.")

        org_id = str(user.get("organization_id", ""))
        org = await org_db.get_by_id(org_id)

        if not org:
            raise HTTPException(status_code=403, detail="No organization found")

        organizations = [OrganizationSummary(
            id=org_id,
            name=org.get("name", ""),
            slug=org.get("slug", "")
        )]

        handoff_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=5)

        await user_db.set_handoff_token(
            user_id=str(user["_id"]),
            token=handoff_token,
            expires_at=expires_at
        )

        await audit_logger.log_event(
            event_type="login_central",
            user_id=str(user["_id"]),
            email=login_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"org_count": len(organizations)},
            organization_id=org_id
        )

        logger.info(f"Central login successful: {login_data.email} (ID: {user['_id']})")

        return CentralLoginResponse(
            user_id=str(user["_id"]),
            email=login_data.email,
            organizations=organizations,
            handoff_token=handoff_token,
            handoff_expires_in=300
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during central login")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/exchange-token", response_model=AuthResponse)
@limiter.limit("10/minute")
async def exchange_token(
    request: Request,
    data: ExchangeTokenRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Exchange handoff token for JWT. Called by tenant app after redirect from marketing site."""
    try:
        ip_address, user_agent = get_client_info(request)

        user = await user_db.validate_handoff_token(data.token)

        if not user:
            await audit_logger.log_event(
                event_type="token_exchange",
                user_id=None,
                email=None,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Invalid or expired token"}
            )
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        org_id = str(user.get("organization_id", ""))
        org = await org_db.get_by_id(org_id)

        if not org or org.get("slug") != data.organization_slug:
            await audit_logger.log_event(
                event_type="token_exchange",
                user_id=str(user["_id"]),
                email=user.get("email"),
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Not authorized for organization", "org_slug": data.organization_slug}
            )
            raise HTTPException(status_code=403, detail="Not authorized for this organization")

        await user_db.clear_handoff_token(str(user["_id"]))

        access_token = create_access_token(
            data={
                "sub": str(user["_id"]),
                "email": user.get("email"),
                "role": user.get("role", "user"),
                "organization_id": org_id,
                "organization_slug": org.get("slug", "")
            }
        )

        await audit_logger.log_event(
            event_type="token_exchange",
            user_id=str(user["_id"]),
            email=user.get("email"),
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"org_slug": data.organization_slug},
            organization_id=org_id
        )

        logger.info(f"Token exchange successful: {user.get('email')} → {data.organization_slug}")

        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            user_id=str(user["_id"]),
            email=user.get("email", ""),
            organization=OrganizationResponse(
                id=org_id,
                name=org.get("name", ""),
                slug=org.get("slug", ""),
                branding=org.get("branding", {}),
                workflows=org.get("workflows", {})
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during token exchange")
        raise HTTPException(status_code=500, detail="Internal server error")
