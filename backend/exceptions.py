"""Global exception handlers - generic errors for security (#5)"""
import uuid
import logging
import traceback
from datetime import datetime
from fastapi import Request, HTTPException, FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log full error, return generic message to client"""
    request_id = str(uuid.uuid4())

    logger.error(
        f"Request failed: {request.method} {request.url.path}",
        extra={
            "request_id": request_id,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc()
        }
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "An error occurred while processing your request",
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Handle Pydantic validation errors"""
    request_id = str(uuid.uuid4())

    logger.warning(
        f"Validation error: {request.method} {request.url.path}",
        extra={"request_id": request_id, "errors": exc.errors()}
    )

    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation failed",
            "details": exc.errors(),
            "request_id": request_id
        }
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """5xx: generic error, 4xx: specific error"""
    request_id = str(uuid.uuid4())

    if exc.status_code >= 500:
        logger.error(
            f"HTTP {exc.status_code}: {request.method} {request.url.path}",
            extra={"request_id": request_id, "detail": exc.detail}
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "An error occurred",
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "request_id": request_id}
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers"""
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
