import base64
import io
import os
import secrets
from datetime import datetime, timedelta

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from loguru import logger
from pydantic import BaseModel
from slowapi import Limiter

from backend.audit import AuditLogger
from backend.dependencies import (
    get_audit_logger_dep,
    get_client_info,
    get_current_user,
    get_organization_db,
    get_user_db,
    get_user_id_from_request,
)
from backend.models import AsyncUserRecord
from backend.models.organization import AsyncOrganizationRecord

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
    subdomain: str  # URL-friendly identifier (hyphens, e.g., "demo-clinic-alpha")
    branding: dict
    workflows: dict  # Contains workflow configs with patient_schema

class AuthResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    email: str
    organization: OrganizationResponse
    is_super_admin: bool = False

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
    subdomain: str  # URL-friendly identifier for redirects


class CentralLoginResponse(BaseModel):
    user_id: str
    email: str
    organizations: list[OrganizationSummary]
    handoff_token: str
    handoff_expires_in: int


class ExchangeTokenRequest(BaseModel):
    token: str
    organization_slug: str


# MFA Models
class MFASetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    qr_code: str  # Base64-encoded PNG


class MFAVerifyRequest(BaseModel):
    code: str


class MFAVerifyResponse(BaseModel):
    success: bool
    backup_codes: list[str]


class MFAStatusResponse(BaseModel):
    enabled: bool
    backup_codes_remaining: int


class MFALoginRequest(BaseModel):
    mfa_token: str
    code: str


class MFARequiredResponse(BaseModel):
    mfa_required: bool
    mfa_token: str
    expires_in: int


class MFADisableRequest(BaseModel):
    password: str


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_mfa_token(user_id: str, email: str, organization_id: str) -> str:
    """Create short-lived token for MFA verification step."""
    expire = datetime.utcnow() + timedelta(minutes=5)
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "organization_id": organization_id,
            "type": "mfa_challenge",
            "exp": expire
        },
        SECRET_KEY,
        algorithm=ALGORITHM
    )


def verify_mfa_token(token: str) -> dict | None:
    """Verify MFA challenge token. Returns payload or None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "mfa_challenge":
            return None
        return payload
    except JWTError:
        return None


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    login_data: LoginRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Login endpoint. Returns MFARequiredResponse if MFA enabled, else AuthResponse."""
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
                    detail="Account locked due to too many failed attempts. Contact support."
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

        # Check if MFA is enabled
        if user.get("mfa_enabled"):
            mfa_token = create_mfa_token(user_id, login_data.email, organization_id)

            await audit_logger.log_event(
                event_type="login",
                user_id=user_id,
                email=login_data.email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=True,
                details={"mfa_required": True, "role": user.get("role", "admin")},
                organization_id=organization_id
            )

            logger.info(f"MFA challenge issued for: {login_data.email}")
            return MFARequiredResponse(
                mfa_required=True,
                mfa_token=mfa_token,
                expires_in=300
            )

        # No MFA - complete login
        await audit_logger.log_event(
            event_type="login",
            user_id=user_id,
            email=login_data.email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"role": user.get("role", "admin")},
            organization_id=organization_id
        )

        access_token = create_access_token(
            data={
                "sub": user_id,
                "email": login_data.email,
                "role": user.get("role", "admin"),
                "organization_id": organization_id,
                "organization_slug": organization_slug,
                "is_super_admin": user.get("is_super_admin", False)
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
                subdomain=org.get("subdomain", organization_slug.replace("_", "-")),
                branding=org.get("branding", {}),
                workflows=org.get("workflows", {})
            ),
            is_super_admin=user.get("is_super_admin", False)
        )

    except HTTPException:
        raise
    except Exception:
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
                    detail="Account locked due to too many failed attempts. Contact support."
                )

            raise HTTPException(status_code=401, detail="Invalid email or password")

        if user.get("status") == "locked":
            raise HTTPException(status_code=403, detail="Account is locked. Contact support.")

        if user.get("status") == "inactive":
            raise HTTPException(status_code=403, detail="Account is inactive. Contact support.")

        org_id = str(user.get("organization_id", ""))
        org = await org_db.get_by_id(org_id)

        if not org:
            raise HTTPException(status_code=403, detail="No organization found")

        org_slug = org.get("slug", "")
        organizations = [OrganizationSummary(
            id=org_id,
            name=org.get("name", ""),
            slug=org_slug,
            subdomain=org.get("subdomain", org_slug.replace("_", "-"))
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
    except Exception:
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

        # Accept either slug or subdomain for org validation
        org_slug = org.get("slug", "") if org else ""
        org_subdomain = org.get("subdomain", org_slug.replace("_", "-")) if org else ""
        requested_org = data.organization_slug

        if not org or (requested_org != org_slug and requested_org != org_subdomain):
            await audit_logger.log_event(
                event_type="token_exchange",
                user_id=str(user["_id"]),
                email=user.get("email"),
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Not authorized for org", "org_slug": data.organization_slug}
            )
            raise HTTPException(status_code=403, detail="Not authorized for this organization")

        await user_db.clear_handoff_token(str(user["_id"]))

        access_token = create_access_token(
            data={
                "sub": str(user["_id"]),
                "email": user.get("email"),
                "role": user.get("role", "admin"),
                "organization_id": org_id,
                "organization_slug": org.get("slug", ""),
                "is_super_admin": user.get("is_super_admin", False)
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
                subdomain=org.get("subdomain", org.get("slug", "").replace("_", "-")),
                branding=org.get("branding", {}),
                workflows=org.get("workflows", {})
            ),
            is_super_admin=user.get("is_super_admin", False)
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during token exchange")
        raise HTTPException(status_code=500, detail="Internal server error")


# -----------------------------------------------------------------------------
# MFA Endpoints
# -----------------------------------------------------------------------------

@router.post("/login/mfa", response_model=AuthResponse)
@limiter.limit("10/minute")
async def login_mfa(
    request: Request,
    mfa_data: MFALoginRequest,
    user_db: AsyncUserRecord = Depends(get_user_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Complete login with MFA code after receiving mfa_token from /login."""
    try:
        ip_address, user_agent = get_client_info(request)

        # Verify MFA token
        payload = verify_mfa_token(mfa_data.mfa_token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired MFA token")

        user_id = payload.get("sub")
        email = payload.get("email")
        organization_id = payload.get("organization_id")

        # Verify MFA code
        is_valid = await user_db.verify_mfa_code(user_id, mfa_data.code)
        if not is_valid:
            await audit_logger.log_event(
                event_type="mfa_verify",
                user_id=user_id,
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Invalid MFA code"},
                organization_id=organization_id
            )
            raise HTTPException(status_code=401, detail="Invalid MFA code")

        # Get user and org for token
        user = await user_db.find_user_by_email(email)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        org = await org_db.get_by_id(organization_id)
        if not org:
            raise HTTPException(status_code=403, detail="Organization not found")

        organization_slug = org.get("slug", "")

        await audit_logger.log_event(
            event_type="mfa_verify",
            user_id=user_id,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={},
            organization_id=organization_id
        )

        access_token = create_access_token(
            data={
                "sub": user_id,
                "email": email,
                "role": user.get("role", "admin"),
                "organization_id": organization_id,
                "organization_slug": organization_slug,
                "is_super_admin": user.get("is_super_admin", False)
            }
        )

        logger.info(f"MFA login completed: {email}")

        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            user_id=user_id,
            email=email,
            organization=OrganizationResponse(
                id=organization_id,
                name=org.get("name", ""),
                slug=organization_slug,
                subdomain=org.get("subdomain", organization_slug.replace("_", "-")),
                branding=org.get("branding", {}),
                workflows=org.get("workflows", {})
            ),
            is_super_admin=user.get("is_super_admin", False)
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during MFA login")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/mfa/setup", response_model=MFASetupResponse)
async def mfa_setup(
    request: Request,
    current_user: dict = Depends(get_current_user),
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Start MFA setup - generates secret and QR code. Requires authentication."""
    try:
        user_id = current_user["sub"]
        email = current_user["email"]
        organization_id = current_user.get("organization_id")
        ip_address, user_agent = get_client_info(request)

        # Check if MFA already enabled
        mfa_status = await user_db.get_mfa_status(user_id)
        if mfa_status.get("enabled"):
            raise HTTPException(status_code=400, detail="MFA is already enabled")

        # Generate secret
        success, secret, provisioning_uri = await user_db.setup_mfa(user_id)
        if not success or not secret or not provisioning_uri:
            raise HTTPException(status_code=500, detail="Failed to generate MFA secret")

        # Generate QR code as base64 PNG
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()

        await audit_logger.log_event(
            event_type="mfa_setup_init",
            user_id=user_id,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={},
            organization_id=organization_id
        )

        logger.info(f"MFA setup initiated for user {user_id}")

        return MFASetupResponse(
            secret=secret,
            provisioning_uri=provisioning_uri,
            qr_code=f"data:image/png;base64,{qr_base64}"
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during MFA setup")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/mfa/verify", response_model=MFAVerifyResponse)
async def mfa_verify(
    request: Request,
    verify_data: MFAVerifyRequest,
    current_user: dict = Depends(get_current_user),
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Complete MFA setup by verifying TOTP code. Returns backup codes."""
    try:
        user_id = current_user["sub"]
        email = current_user["email"]
        organization_id = current_user.get("organization_id")
        ip_address, user_agent = get_client_info(request)

        # Verify and enable MFA
        success, backup_codes = await user_db.verify_and_enable_mfa(user_id, verify_data.code)

        if not success:
            await audit_logger.log_event(
                event_type="mfa_setup_complete",
                user_id=user_id,
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Invalid verification code"},
                organization_id=organization_id
            )
            raise HTTPException(status_code=400, detail="Invalid verification code")

        await audit_logger.log_event(
            event_type="mfa_setup_complete",
            user_id=user_id,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"backup_codes_generated": len(backup_codes)},
            organization_id=organization_id
        )

        logger.info(f"MFA enabled for user {user_id}")

        return MFAVerifyResponse(
            success=True,
            backup_codes=backup_codes
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during MFA verification")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/mfa/disable")
async def mfa_disable(
    request: Request,
    disable_data: MFADisableRequest,
    current_user: dict = Depends(get_current_user),
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Disable MFA. Requires password verification."""
    try:
        user_id = current_user["sub"]
        email = current_user["email"]
        organization_id = current_user.get("organization_id")
        ip_address, user_agent = get_client_info(request)

        # Verify password
        is_valid, user = await user_db.verify_password(email, disable_data.password)
        if not is_valid:
            await audit_logger.log_event(
                event_type="mfa_disable",
                user_id=user_id,
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                details={"reason": "Invalid password"},
                organization_id=organization_id
            )
            raise HTTPException(status_code=401, detail="Invalid password")

        # Disable MFA
        success = await user_db.disable_mfa(user_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to disable MFA")

        await audit_logger.log_event(
            event_type="mfa_disable",
            user_id=user_id,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={},
            organization_id=organization_id
        )

        logger.info(f"MFA disabled for user {user_id}")

        return {"success": True, "message": "MFA has been disabled"}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during MFA disable")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/mfa/status", response_model=MFAStatusResponse)
async def mfa_status(
    current_user: dict = Depends(get_current_user),
    user_db: AsyncUserRecord = Depends(get_user_db)
):
    """Get MFA status for current user."""
    try:
        user_id = current_user["sub"]
        status = await user_db.get_mfa_status(user_id)

        return MFAStatusResponse(
            enabled=status.get("enabled", False),
            backup_codes_remaining=status.get("backup_codes_remaining", 0)
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error getting MFA status")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/mfa/backup-codes")
async def regenerate_backup_codes(
    request: Request,
    current_user: dict = Depends(get_current_user),
    user_db: AsyncUserRecord = Depends(get_user_db),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
):
    """Regenerate MFA backup codes. Requires MFA to be enabled."""
    try:
        user_id = current_user["sub"]
        email = current_user["email"]
        organization_id = current_user.get("organization_id")
        ip_address, user_agent = get_client_info(request)

        success, backup_codes = await user_db.regenerate_backup_codes(user_id)

        if not success:
            raise HTTPException(status_code=400, detail="MFA is not enabled")

        await audit_logger.log_event(
            event_type="mfa_backup_regenerate",
            user_id=user_id,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            details={"backup_codes_generated": len(backup_codes)},
            organization_id=organization_id
        )

        logger.info(f"Backup codes regenerated for user {user_id}")

        return {
            "success": True,
            "backup_codes": backup_codes
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error regenerating backup codes")
        raise HTTPException(status_code=500, detail="Internal server error")
