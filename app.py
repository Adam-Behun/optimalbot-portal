"""Main application entry point"""
import os
import asyncio
import uvicorn
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from backend.config import validate_backend_startup
from backend.main import app

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Healthcare AI Agent - HIPAA Compliance Mode")
    logger.info("=" * 60)

    # Validate environment and service connectivity
    try:
        asyncio.run(validate_backend_startup())
    except RuntimeError as e:
        logger.error(f"❌ Startup validation failed: {e}")
        logger.error("Cannot start application - fix configuration and try again")
        exit(1)

    logger.info("Starting Healthcare AI Agent server...")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))