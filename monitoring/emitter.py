"""
Event emitter mixins and utilities for easy monitoring integration.
"""

import time
from typing import Dict, Optional, Any
from contextlib import asynccontextmanager, contextmanager
import logging

from .collector import emit_event, get_collector

logger = logging.getLogger(__name__)


class MonitoringMixin:
    """
    Mixin to add monitoring capabilities to any class.
    
    Usage:
        class MyService(MonitoringMixin):
            def __init__(self, session_id):
                self.session_id = session_id
            
            def do_work(self):
                self.emit("WORK", "started")
                # ... do work ...
                self.emit("WORK", "completed", latency_ms=100)
    """
    
    session_id: str  # Must be set by inheriting class
    
    def emit(
        self,
        category: str,
        event: str,
        metadata: Dict[str, Any] = None,
        latency_ms: float = None,
        severity: str = "info",
        tags: list = None
    ) -> None:
        """
        Emit a monitoring event.
        
        Args:
            category: Event category
            event: Event name
            metadata: Event metadata
            latency_ms: Optional latency
            severity: Event severity
            tags: Optional tags
        """
        if not hasattr(self, 'session_id'):
            logger.warning(f"{self.__class__.__name__} missing session_id, cannot emit event")
            return
        
        emit_event(
            session_id=self.session_id,
            category=category,
            event=event,
            metadata=metadata,
            latency_ms=latency_ms,
            severity=severity,
            tags=tags
        )


class LatencyTracker:
    """
    Context manager for automatic latency tracking.
    
    Usage:
        with LatencyTracker(session_id, "intent_classification") as tracker:
            result = classify_intent(message)
            tracker.metadata = {"intent": result.intent}
        
        # Or async:
        async with LatencyTracker(session_id, "llm_call", async_mode=True):
            response = await llm.generate()
    """
    
    def __init__(
        self,
        session_id: str,
        stage: str,
        category: str = "LATENCY",
        event: Optional[str] = None,
        metadata: Dict[str, Any] = None,
        async_mode: bool = False
    ):
        """
        Initialize latency tracker.
        
        Args:
            session_id: Session ID
            stage: Stage name (e.g., "intent_classification")
            category: Event category (default: LATENCY)
            event: Event name (default: stage)
            metadata: Additional metadata
            async_mode: Whether to use async context manager
        """
        self.session_id = session_id
        self.stage = stage
        self.category = category
        self.event = event or stage
        self.metadata = metadata or {}
        self.async_mode = async_mode
        
        self.start_time: Optional[float] = None
        self.latency_ms: Optional[float] = None
    
    def __enter__(self):
        """Start timing (sync)."""
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """End timing and emit event (sync)."""
        self.latency_ms = (time.perf_counter() - self.start_time) * 1000
        
        # Add stage and latency to metadata
        self.metadata["stage"] = self.stage
        
        # Emit event
        emit_event(
            session_id=self.session_id,
            category=self.category,
            event=self.event,
            metadata=self.metadata,
            latency_ms=self.latency_ms,
            severity="warning" if self.latency_ms > 1000 else "info"  # Warn if >1s
        )
        
        return False  # Don't suppress exceptions
    
    async def __aenter__(self):
        """Start timing (async)."""
        self.start_time = time.perf_counter()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """End timing and emit event (async)."""
        self.latency_ms = (time.perf_counter() - self.start_time) * 1000
        
        # Add stage and latency to metadata
        self.metadata["stage"] = self.stage
        
        # Emit event
        emit_event(
            session_id=self.session_id,
            category=self.category,
            event=self.event,
            metadata=self.metadata,
            latency_ms=self.latency_ms,
            severity="warning" if self.latency_ms > 1000 else "info"
        )
        
        return False


@contextmanager
def track_latency(
    session_id: str,
    stage: str,
    category: str = "LATENCY",
    metadata: Dict[str, Any] = None
):
    """
    Decorator-friendly context manager for latency tracking.
    
    Usage:
        with track_latency(session_id, "database_query"):
            results = db.query(...)
    """
    tracker = LatencyTracker(session_id, stage, category, metadata=metadata)
    with tracker:
        yield tracker


@asynccontextmanager
async def track_latency_async(
    session_id: str,
    stage: str,
    category: str = "LATENCY",
    metadata: Dict[str, Any] = None
):
    """
    Async version of track_latency.
    
    Usage:
        async with track_latency_async(session_id, "llm_call"):
            response = await llm.generate()
    """
    tracker = LatencyTracker(session_id, stage, category, metadata=metadata, async_mode=True)
    async with tracker:
        yield tracker


def monitored(category: str, event: str = None):
    """
    Decorator to automatically monitor function execution.
    
    Usage:
        @monitored("LLM", "generate")
        async def generate_response(self, prompt):
            # Must have self.session_id
            return await llm.generate(prompt)
    """
    def decorator(func):
        event_name = event or func.__name__
        
        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(self, *args, **kwargs):
                if not hasattr(self, 'session_id'):
                    return await func(self, *args, **kwargs)
                
                async with track_latency_async(self.session_id, event_name, category):
                    return await func(self, *args, **kwargs)
            
            return async_wrapper
        else:
            def sync_wrapper(self, *args, **kwargs):
                if not hasattr(self, 'session_id'):
                    return func(self, *args, **kwargs)
                
                with track_latency(self.session_id, event_name, category):
                    return func(self, *args, **kwargs)
            
            return sync_wrapper
    
    return decorator


# Async import
import asyncio