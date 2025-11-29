import os
import logging
from typing import List, Tuple
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

ENV = os.getenv("ENV", "local")

REQUIRED_BACKEND_ENV_VARS = [
    "JWT_SECRET_KEY",
    "MONGO_URI",
    "ALLOWED_ORIGINS"
]

if ENV == "production":
    REQUIRED_BACKEND_ENV_VARS.append("PIPECAT_API_KEY")


def validate_env_vars(required_vars: List[str]) -> Tuple[bool, List[str]]:
    missing = [var for var in required_vars if not os.getenv(var)]
    return len(missing) == 0, missing


async def health_check_mongodb(uri: str, timeout: float = 5.0) -> Tuple[bool, str]:
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=int(timeout * 1000))
        await client.admin.command('ping')
        client.close()
        return True, ""
    except Exception as e:
        return False, f"MongoDB connection failed: {str(e)}"


async def validate_backend_startup() -> None:
    logger.info("Validating backend environment...")

    all_present, missing = validate_env_vars(REQUIRED_BACKEND_ENV_VARS)
    if not all_present:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    logger.info("✓ Required environment variables present")

    mongo_uri = os.getenv("MONGO_URI")
    is_healthy, error = await health_check_mongodb(mongo_uri)
    if not is_healthy:
        raise RuntimeError(f"MongoDB health check failed: {error}")

    logger.info("✓ MongoDB connection successful")
    logger.info("Backend validation complete - ready to start")
