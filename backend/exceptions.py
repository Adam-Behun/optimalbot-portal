import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import ValidationError


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid.uuid4())
    logger.error(
        f"Request failed: {request.method} {request.url.path} | "
        f"request_id={request_id} error_type={type(exc).__name__} error={exc}"
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
    request_id = str(uuid.uuid4())
    logger.warning(
        f"Validation error: {request.method} {request.url.path} | "
        f"request_id={request_id} errors={exc.errors()}"
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
    request_id = str(uuid.uuid4())

    if exc.status_code >= 500:
        logger.error(
            f"HTTP {exc.status_code}: {request.method} {request.url.path} | "
            f"request_id={request_id} detail={exc.detail}"
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
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
