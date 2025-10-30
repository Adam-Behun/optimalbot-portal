"""FastAPI application - modular architecture"""
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.lifespan import lifespan
from backend.middleware import SecurityHeadersMiddleware
from backend.exceptions import register_exception_handlers
from backend.dependencies import get_user_id_from_request
from backend.api import health, auth, patients, calls

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Validate required env vars
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY environment variable is required")
if len(SECRET_KEY) < 32:
    raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters")

ALLOWED_ORIGINS_STR = os.getenv("ALLOWED_ORIGINS")
if not ALLOWED_ORIGINS_STR:
    raise RuntimeError("ALLOWED_ORIGINS required")

# Initialize FastAPI
app = FastAPI(
    title="Healthcare AI Agent",
    version="1.0.0",
    lifespan=lifespan
)

# Rate limiting
limiter = Limiter(key_func=get_user_id_from_request)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
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

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# Exception handlers
register_exception_handlers(app)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(patients.router, prefix="/patients", tags=["Patients"])
app.include_router(calls.router, tags=["Calls"])
