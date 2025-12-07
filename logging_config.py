import os
import sys
import logging
from loguru import logger

NOISY_LIBRARIES = ['pymongo', 'websockets', 'websockets.client', 'httpx', 'httpcore', 'urllib3']


def setup_logging(debug: bool = None):
    if debug is None:
        debug = os.getenv("DEBUG", "false").lower() in ["true", "1", "yes"]

    env = os.getenv("ENV", "local")
    level = "DEBUG" if debug else "INFO"

    logger.remove()

    if env == "production":
        logger.add(sys.stderr, level=level, format="{message}", serialize=True)
    else:
        logger.add(
            sys.stderr,
            level=level,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        )

    for lib in NOISY_LIBRARIES:
        logging.getLogger(lib).setLevel(logging.WARNING)

    if debug:
        logger.info(f"Debug logging enabled (env={env})")
