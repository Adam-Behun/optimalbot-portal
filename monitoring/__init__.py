"""
Monitoring package for OpenTelemetry tracing.
Integrates with Pipecat's built-in tracing system.
"""

from .otel_setup import initialize_otel_tracing
from .otel_processor import MongoDBSpanProcessor

__all__ = [
    "initialize_otel_tracing",
    "MongoDBSpanProcessor",
]