"""
OpenTelemetry setup for Pipecat with MongoDB persistence.
Adds custom processors to Pipecat's existing TracerProvider.
"""

import logging
from opentelemetry import trace
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, BatchSpanProcessor

from .otel_processor import MongoDBSpanProcessor

logger = logging.getLogger(__name__)

_otel_initialized = False


def initialize_otel_tracing(console_debug: bool = False):
    """
    Add custom processors to Pipecat's existing TracerProvider.
    Call this ONCE at application startup, AFTER setup_tracing().
    
    Example:
        # In FastAPI/Flask app startup
        @app.on_event("startup")
        async def startup():
            setup_tracing(service_name="my-service", exporter=None)
            initialize_otel_tracing(console_debug=True)
    """
    global _otel_initialized
    
    if _otel_initialized:
        logger.debug("Custom OpenTelemetry processors already initialized")
        return
    
    # Get the existing tracer provider (set by Pipecat's setup_tracing)
    tracer_provider = trace.get_tracer_provider()
    
    # Verify we have a valid TracerProvider
    if not hasattr(tracer_provider, 'add_span_processor'):
        logger.error(
            "❌ TracerProvider doesn't support add_span_processor. "
            "Ensure setup_tracing() was called first."
        )
        return
    
    # Add console exporter for real-time debugging (optional)
    if console_debug:
        console_exporter = ConsoleSpanExporter()
        console_processor = BatchSpanProcessor(console_exporter)
        tracer_provider.add_span_processor(console_processor)
        logger.info("✅ Console span exporter enabled")
    
    # Add MongoDB processor for post-call persistence
    mongo_processor = MongoDBSpanProcessor(console_debug=console_debug)
    tracer_provider.add_span_processor(mongo_processor)
    logger.info("✅ MongoDB span processor enabled")
    
    _otel_initialized = True
    logger.info("✅ Custom OpenTelemetry processors added to existing TracerProvider")