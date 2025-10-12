"""
Monitoring system for voice AI pipeline.

Usage:
    # Initialize in app.py
    from monitoring import get_collector
    collector = get_collector()
    await collector.start_cleanup_task()
    
    # Emit events
    from monitoring import emit_event
    emit_event(
        session_id="sess_123",
        category="INTENT",
        event="detected",
        metadata={"intent": "rep_greeted_caller"},
        latency_ms=87.3
    )
    
    # Track latency
    from monitoring import LatencyTracker
    async with LatencyTracker(session_id, "llm_call") as tracker:
        response = await llm.generate()
        tracker.metadata = {"tokens": len(response)}
    
    # Use mixin
    from monitoring import MonitoringMixin
    class MyService(MonitoringMixin):
        def __init__(self, session_id):
            self.session_id = session_id
        
        def do_work(self):
            self.emit("WORK", "started")
"""

from .models import (
    MonitoringEvent,
    CallEvent,
    IntentEvent,
    TransitionEvent,
    PromptEvent,
    LLMEvent,
    TranscriptEvent,
    LatencyEvent,
    ErrorEvent,
    CallMetrics,
    LatencyMetrics,
    IntentMetrics
)

from .collector import (
    MonitoringCollector,
    get_collector,
    emit_event
)

from .emitter import (
    MonitoringMixin,
    LatencyTracker,
    track_latency,
    track_latency_async,
    monitored
)

__all__ = [
    # Models
    "MonitoringEvent",
    "CallEvent",
    "IntentEvent",
    "TransitionEvent",
    "PromptEvent",
    "LLMEvent",
    "TranscriptEvent",
    "LatencyEvent",
    "ErrorEvent",
    "CallMetrics",
    "LatencyMetrics",
    "IntentMetrics",
    
    # Collector
    "MonitoringCollector",
    "get_collector",
    "emit_event",
    
    # Emitter
    "MonitoringMixin",
    "LatencyTracker",
    "track_latency",
    "track_latency_async",
    "monitored",
]