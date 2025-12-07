import asyncio
from contextlib import asynccontextmanager
import aiohttp
from fastapi import FastAPI
from loguru import logger

from backend.database import close_mongo_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting...")

    app.state.http_session = aiohttp.ClientSession()
    logger.info("HTTP session created")

    logger.info("Application ready")

    yield

    logger.info("Shutdown signal received...")
    await app.state.http_session.close()
    logger.info("HTTP session closed")
    await asyncio.sleep(2)
    await close_mongo_client()
    logger.info("Graceful shutdown complete")
