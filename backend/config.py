import os
from typing import List, Tuple
from loguru import logger

ENV = os.getenv("ENV", "local")

REQUIRED_BACKEND_ENV_VARS = [
    "JWT_SECRET_KEY",
    "MONGO_URI",
    "ALLOWED_ORIGINS"
]

# Require PIPECAT_API_KEY for any non-local environment
if ENV in ("production", "test"):
    REQUIRED_BACKEND_ENV_VARS.append("PIPECAT_API_KEY")


def validate_env_vars(required_vars: List[str]) -> Tuple[bool, List[str]]:
    missing = [var for var in required_vars if not os.getenv(var)]
    return len(missing) == 0, missing


async def validate_backend_startup() -> None:
    from backend.database import check_connection

    logger.info("Validating backend environment...")

    all_present, missing = validate_env_vars(REQUIRED_BACKEND_ENV_VARS)
    if not all_present:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    logger.info("✓ Required environment variables present")

    secret_key = os.getenv("JWT_SECRET_KEY", "")
    if len(secret_key) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters")

    is_healthy, error = await check_connection()
    if not is_healthy:
        raise RuntimeError(f"MongoDB health check failed: {error}")

    logger.info("✓ MongoDB connection successful")
    logger.info("Backend validation complete - ready to start")
