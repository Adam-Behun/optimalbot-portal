import os
import logging
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_mongo_client: Optional[AsyncIOMotorClient] = None

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "alfons")
MONGO_MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", "10"))
MONGO_SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))


def get_mongo_client() -> AsyncIOMotorClient:
    global _mongo_client
    if _mongo_client is None:
        logger.info(f"Initializing shared MongoDB client (pool size: {MONGO_MAX_POOL_SIZE})")
        _mongo_client = AsyncIOMotorClient(
            MONGO_URI,
            maxPoolSize=MONGO_MAX_POOL_SIZE,
            serverSelectionTimeoutMS=MONGO_SERVER_SELECTION_TIMEOUT_MS
        )
    return _mongo_client


def get_database():
    return get_mongo_client()[MONGO_DB_NAME]


async def close_mongo_client():
    global _mongo_client
    if _mongo_client is not None:
        logger.info("Closing shared MongoDB client connection")
        _mongo_client.close()
        _mongo_client = None
