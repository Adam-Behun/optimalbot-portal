import os
from typing import List, Tuple, Union

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from loguru import logger

from backend.audit import AuditLogger, get_audit_logger
from backend.models import (
    AsyncPatientRecord,
    AsyncUserRecord,
    get_async_patient_db,
    get_async_user_db,
)
from backend.models.organization import AsyncOrganizationRecord, get_async_organization_db
from backend.sessions import AsyncSessionRecord, get_async_session_db

security = HTTPBearer()
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ALGORITHM = "HS256"


def get_patient_db() -> AsyncPatientRecord:
    return get_async_patient_db()


def get_user_db() -> AsyncUserRecord:
    return get_async_user_db()


def get_session_db() -> AsyncSessionRecord:
    return get_async_session_db()


def get_audit_logger_dep() -> AuditLogger:
    return get_audit_logger()


def get_organization_db() -> AsyncOrganizationRecord:
    return get_async_organization_db()


def get_client_info(request: Request) -> Tuple[str, str]:
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
    audit_logger = get_audit_logger()
    ip_address, user_agent = get_client_info(request)
    await audit_logger.log_phi_access(
        user_id=user["sub"],
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        user_agent=user_agent,
        endpoint=request.url.path,
        organization_id=user.get("organization_id")
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    audit_logger: AuditLogger = Depends(get_audit_logger_dep)
) -> dict:
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


def require_role(roles: Union[str, List[str]]):
    """
    Dependency factory that requires user to have one of the specified roles.

    Usage:
        @router.get("/admin-only")
        async def admin_endpoint(
            current_user: dict = Depends(require_role("admin"))
        ):
            ...

        @router.get("/multi-role")
        async def multi_role_endpoint(
            current_user: dict = Depends(require_role(["admin", "user"]))
        ):
            ...
    """
    if isinstance(roles, str):
        roles = [roles]

    async def role_checker(
        current_user: dict = Depends(get_current_user)
    ) -> dict:
        user_role = current_user.get("role")
        if user_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role: {', '.join(roles)}"
            )
        return current_user

    return role_checker


def get_current_user_organization_id(current_user: dict = Depends(get_current_user)) -> str:
    organization_id = current_user.get("organization_id")
    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not associated with an organization"
        )
    return organization_id


async def require_organization_access(
    current_user: dict = Depends(get_current_user),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db)
) -> dict:
    organization_id = current_user.get("organization_id")
    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not associated with an organization"
        )

    org = await org_db.get_by_id(organization_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found"
        )

    return {
        "user": current_user,
        "organization": org,
        "organization_id": organization_id
    }


def get_user_id_from_request(request: Request) -> str:
    try:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return f"user:{payload.get('sub')}"
    except Exception:
        pass

    ip_address = get_client_info(request)[0]
    return f"ip:{ip_address}"
