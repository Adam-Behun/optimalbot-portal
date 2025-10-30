"""Application lifecycle management"""
import os
import base64
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from pipecat.utils.tracing.setup import setup_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic"""
    # Startup
    logger.info("🚀 Application starting...")

    # Note: DB clients are initialized lazily via singletons on first use
    # We don't initialize them here to avoid import-time dependencies

    # Setup OpenTelemetry tracing with Langfuse
    public_key = os.getenv('LANGFUSE_PUBLIC_KEY')
    secret_key = os.getenv('LANGFUSE_SECRET_KEY')
    if public_key and secret_key:
        auth_string = f"{public_key}:{secret_key}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()

        langfuse_exporter = OTLPSpanExporter(
            endpoint=f"{os.getenv('LANGFUSE_HOST')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {encoded_auth}"}
        )

        console_export = os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true"
        setup_tracing(
            service_name="voice-ai-pipeline",
            exporter=langfuse_exporter,
            console_export=console_export
        )
        logger.info("✅ OpenTelemetry tracing configured")

    logger.info("✅ Application ready")

    yield

    # Shutdown
    logger.info("Shutdown signal received...")
    await asyncio.sleep(2)

    # DB connections will be closed by Motor/MongoDB driver automatically
    logger.info("Graceful shutdown complete")
