"""
OpenTelemetry setup with console logging and MongoDB persistence.
Provides real-time span visibility during calls and post-call storage.
"""

import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

from .otel_processor import MongoDBSpanProcessor

logger = logging.getLogger(__name__)

_otel_initialized = False


def initialize_otel_tracing(console_debug: bool = False):
    """
    Initialize OpenTelemetry with console + MongoDB exporters.
    
    Args:
        console_debug: If True, prints spans to console for real-time debugging
    
    Call once at application startup.
    """
    global _otel_initialized
    
    if _otel_initialized:
        logger.debug("OpenTelemetry already initialized")
        return
    
    # Create resource with service info
    resource = Resource.create({
        "service.name": "voice-ai-pipeline",
        "service.version": "1.0.0"
    })
    
    # Setup tracer provider
    tracer_provider = TracerProvider(resource=resource)
    
    # Add console exporter for real-time debugging
    if console_debug:
        console_exporter = ConsoleSpanExporter()
        console_processor = BatchSpanProcessor(console_exporter)
        tracer_provider.add_span_processor(console_processor)
        logger.info("✅ Console span exporter enabled")
    
    # Add MongoDB processor for post-call persistence
    mongo_processor = MongoDBSpanProcessor(console_debug=console_debug)
    tracer_provider.add_span_processor(mongo_processor)
    logger.info("✅ MongoDB span processor enabled")
    
    # Set as global tracer
    trace.set_tracer_provider(tracer_provider)
    
    _otel_initialized = True
    logger.info("✅ OpenTelemetry tracing initialized")


def get_tracer(name: str = "voice-ai"):
    """Get tracer instance for manual span creation"""
    return trace.get_tracer(name)


def add_span_attributes(**attributes):
    """
    Add custom attributes to current span.
    Useful for tracking conversation state, patient data, etc.
    """
    span = trace.get_current_span()
    if span and span.is_recording():
        for key, value in attributes.items():
            if value is not None:  # Skip None values
                span.set_attribute(key, value)