import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.api import (
    admin,
    auth,
    dialin,
    dialout,
    health,
    metrics,
    patients,
    sessions,
    sms,
    webhooks,
)
from backend.costs import router as costs_router
from backend.dependencies import get_user_id_from_request
from backend.exceptions import register_exception_handlers
from backend.lifespan import lifespan
from backend.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from logging_config import setup_logging

setup_logging()

# Use defaults for import-time (allows syntax checking), validate at startup via lifespan
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ALLOWED_ORIGINS_STR = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")

app = FastAPI(
    title="Healthcare AI Agent",
    version="1.0.0",
    lifespan=lifespan
)

limiter = Limiter(key_func=get_user_id_from_request)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

allowed_origins = [origin.strip() for origin in ALLOWED_ORIGINS_STR.split(",")]
logger.info(f"CORS allowed origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)  # outermost - wraps all requests
register_exception_handlers(app)

app.include_router(health.router, tags=["Health"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(patients.router, prefix="/patients", tags=["Patients"])
app.include_router(sessions.router, prefix="/sessions", tags=["Sessions"])
app.include_router(metrics.router, prefix="/metrics", tags=["Metrics"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(dialout.router, tags=["Dial-Out"])
app.include_router(dialin.router, tags=["Dial-In"])
app.include_router(sms.router, tags=["SMS"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(costs_router, prefix="/admin", tags=["Admin Costs"])
