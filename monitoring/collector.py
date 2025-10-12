"""
Central monitoring event collector.
Single source of truth for all monitoring events.
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict, deque
import asyncio
from contextlib import asynccontextmanager

from .models import (
    MonitoringEvent,
    CallMetrics,
    LatencyMetrics,
    IntentMetrics
)

logger = logging.getLogger(__name__)


class MonitoringCollector:
    """
    Central collector for all monitoring events.
    Thread-safe, async-compatible, minimal overhead.
    """
    
    def __init__(
        self,
        max_events_per_session: int = 1000,
        retention_seconds: int = 300,
        enable_console_logging: bool = True
    ):
        """
        Initialize monitoring collector.
        
        Args:
            max_events_per_session: Max events to keep per session (circular buffer)
            retention_seconds: How long to keep events after call ends
            enable_console_logging: Log events to console
        """
        self.max_events_per_session = max_events_per_session
        self.retention_seconds = retention_seconds
        self.enable_console_logging = enable_console_logging
        
        # Event storage: session_id -> deque of events (circular buffer)
        self._events: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_events_per_session)
        )
        
        # Session metadata
        self._sessions: Dict[str, Dict] = {}
        
        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        
        logger.info(f"MonitoringCollector initialized (max_events={max_events_per_session})")
    
    def emit(self, event: MonitoringEvent) -> None:
        """
        Emit a monitoring event.
        
        Args:
            event: MonitoringEvent to emit
        """
        # Store event
        self._events[event.session_id].append(event)
        
        # Update session metadata
        if event.session_id not in self._sessions:
            self._sessions[event.session_id] = {
                "started_at": event.timestamp,
                "last_event_at": event.timestamp,
                "ended": False
            }
        else:
            self._sessions[event.session_id]["last_event_at"] = event.timestamp
        
        # Mark session as ended if this is a call_ended event
        if event.category == "CALL" and event.event == "call_ended":
            self._sessions[event.session_id]["ended"] = True
            self._sessions[event.session_id]["ended_at"] = event.timestamp
        
        # Console logging
        if self.enable_console_logging:
            self._log_to_console(event)
    
    def _log_to_console(self, event: MonitoringEvent) -> None:
        """Format and log event to console."""
        # Color codes for terminal
        colors = {
            "CALL": "\033[95m",        # Magenta
            "INTENT": "\033[94m",      # Blue
            "TRANSITION": "\033[92m",  # Green
            "PROMPT": "\033[96m",      # Cyan
            "LLM": "\033[93m",         # Yellow
            "TRANSCRIPT": "\033[97m",  # White
            "LATENCY": "\033[91m",     # Red
            "ERROR": "\033[91m",       # Red
        }
        
        color = colors.get(event.category, "")
        reset = "\033[0m"
        
        # Format message based on category
        if event.category == "INTENT":
            msg = f"{color}[INTENT]{reset} {event.metadata.get('intent')} ({event.latency_ms:.1f}ms) | \"{event.metadata.get('message', '')[:60]}...\""
        
        elif event.category == "TRANSITION":
            msg = f"{color}[TRANSITION]{reset} {event.metadata.get('from_state')} → {event.metadata.get('to_state')} | Trigger: {event.metadata.get('trigger')}"
        
        elif event.category == "LATENCY":
            msg = f"{color}[LATENCY]{reset} {event.metadata.get('stage')}: {event.latency_ms:.1f}ms"
        
        elif event.category == "ERROR":
            msg = f"{color}[ERROR]{reset} {event.metadata.get('error_type')}: {event.metadata.get('error_message')}"
        
        else:
            msg = f"{color}[{event.category}]{reset} {event.event}"
            if event.latency_ms:
                msg += f" ({event.latency_ms:.1f}ms)"
        
        # Log at appropriate level
        if event.severity == "error":
            logger.error(msg)
        elif event.severity == "warning":
            logger.warning(msg)
        else:
            logger.info(msg)
    
    def get_events(
        self,
        session_id: str,
        category: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[MonitoringEvent]:
        """
        Get events for a session.
        
        Args:
            session_id: Session to get events for
            category: Optional category filter
            limit: Max number of events to return
            
        Returns:
            List of events (most recent first)
        """
        if session_id not in self._events:
            return []
        
        events = list(self._events[session_id])
        
        # Filter by category
        if category:
            events = [e for e in events if e.category == category]
        
        # Reverse to get most recent first
        events.reverse()
        
        # Apply limit
        if limit:
            events = events[:limit]
        
        return events
    
    def get_active_sessions(self) -> List[str]:
        """Get list of active (non-ended) session IDs."""
        return [
            session_id
            for session_id, metadata in self._sessions.items()
            if not metadata.get("ended", False)
        ]
    
    def get_session_metadata(self, session_id: str) -> Optional[Dict]:
        """Get metadata for a session."""
        return self._sessions.get(session_id)
    
    def get_call_metrics(self, session_id: str) -> Optional[CallMetrics]:
        """
        Calculate aggregated metrics for a call.
        
        Args:
            session_id: Session to calculate metrics for
            
        Returns:
            CallMetrics or None if session not found
        """
        if session_id not in self._events:
            return None
        
        events = list(self._events[session_id])
        
        # Calculate duration
        session_meta = self._sessions.get(session_id, {})
        started_at = session_meta.get("started_at")
        ended_at = session_meta.get("ended_at", datetime.utcnow())
        
        total_duration = (ended_at - started_at).total_seconds() if started_at else 0
        
        # Count events by category
        transitions = len([e for e in events if e.category == "TRANSITION"])
        intents = len([e for e in events if e.category == "INTENT"])
        llm_calls = len([e for e in events if e.category == "LLM"])
        transcripts = len([e for e in events if e.category == "TRANSCRIPT"])
        errors = len([e for e in events if e.category == "ERROR"])
        
        # Calculate avg E2E latency
        latency_events = [e for e in events if e.category == "LATENCY" and e.metadata.get("stage") == "e2e"]
        avg_e2e_latency = (
            sum(e.latency_ms for e in latency_events) / len(latency_events)
            if latency_events else None
        )
        
        # Get current state from most recent transition
        transition_events = [e for e in events if e.category == "TRANSITION"]
        current_state = (
            transition_events[-1].metadata.get("to_state")
            if transition_events else None
        )
        
        return CallMetrics(
            session_id=session_id,
            total_duration_seconds=total_duration,
            state_transitions=transitions,
            intents_detected=intents,
            avg_e2e_latency_ms=avg_e2e_latency,
            llm_calls=llm_calls,
            transcript_messages=transcripts,
            errors=errors,
            current_state=current_state
        )
    
    def get_latency_metrics(self, session_id: str) -> Optional[LatencyMetrics]:
        """
        Calculate latency metrics for a call.
        
        Args:
            session_id: Session to calculate metrics for
            
        Returns:
            LatencyMetrics or None if session not found
        """
        if session_id not in self._events:
            return None
        
        events = list(self._events[session_id])
        
        # Group latency events by stage
        latencies: Dict[str, List[float]] = defaultdict(list)
        
        for event in events:
            if event.latency_ms is not None:
                # Intent events
                if event.category == "INTENT":
                    latencies["intent"].append(event.latency_ms)
                
                # LLM events
                elif event.category == "LLM":
                    if event.event == "llm_response_started":
                        latencies["llm_ttft"].append(event.latency_ms)
                    elif event.event == "llm_response_completed":
                        latencies["llm_total"].append(event.latency_ms)
                
                # Latency events
                elif event.category == "LATENCY":
                    stage = event.metadata.get("stage")
                    if stage:
                        latencies[stage].append(event.latency_ms)
        
        # Calculate averages
        def avg(values: List[float]) -> Optional[float]:
            return sum(values) / len(values) if values else None
        
        def percentile(values: List[float], p: int) -> Optional[float]:
            if not values:
                return None
            sorted_values = sorted(values)
            index = int(len(sorted_values) * p / 100)
            return sorted_values[min(index, len(sorted_values) - 1)]
        
        e2e_latencies = latencies.get("e2e", [])
        
        return LatencyMetrics(
            session_id=session_id,
            avg_stt_ms=avg(latencies.get("stt", [])),
            avg_intent_ms=avg(latencies.get("intent", [])),
            avg_llm_ttft_ms=avg(latencies.get("llm_ttft", [])),
            avg_llm_total_ms=avg(latencies.get("llm_total", [])),
            avg_tts_ms=avg(latencies.get("tts", [])),
            avg_e2e_ms=avg(e2e_latencies),
            p50_e2e_ms=percentile(e2e_latencies, 50),
            p95_e2e_ms=percentile(e2e_latencies, 95),
            p99_e2e_ms=percentile(e2e_latencies, 99),
            samples=len(e2e_latencies)
        )
    
    def get_intent_metrics(self, session_id: str) -> Optional[IntentMetrics]:
        """
        Calculate intent classification metrics for a call.
        
        Args:
            session_id: Session to calculate metrics for
            
        Returns:
            IntentMetrics or None if session not found
        """
        if session_id not in self._events:
            return None
        
        events = list(self._events[session_id])
        intent_events = [e for e in events if e.category == "INTENT"]
        
        if not intent_events:
            return IntentMetrics(session_id=session_id)
        
        # Group by intent
        breakdown: Dict[str, Dict] = defaultdict(lambda: {
            "count": 0,
            "latencies": [],
            "methods": {"llm": 0, "keyword": 0, "unknown": 0}
        })
        
        for event in intent_events:
            intent = event.metadata.get("intent", "unknown")
            method = event.metadata.get("method", "unknown")
            
            breakdown[intent]["count"] += 1
            breakdown[intent]["methods"][method] += 1
            
            if event.latency_ms:
                breakdown[intent]["latencies"].append(event.latency_ms)
        
        # Calculate averages
        intent_breakdown = {}
        for intent, data in breakdown.items():
            avg_latency = (
                sum(data["latencies"]) / len(data["latencies"])
                if data["latencies"] else 0
            )
            
            intent_breakdown[intent] = {
                "count": data["count"],
                "avg_latency_ms": avg_latency,
                "method_breakdown": data["methods"]
            }
        
        # Overall stats
        total = len(intent_events)
        llm_count = sum(d["methods"]["llm"] for d in breakdown.values())
        all_latencies = [e.latency_ms for e in intent_events if e.latency_ms]
        avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0
        
        return IntentMetrics(
            session_id=session_id,
            intent_breakdown=intent_breakdown,
            total_classifications=total,
            llm_usage_percent=(llm_count / total * 100) if total > 0 else 0,
            avg_latency_ms=avg_latency
        )
    
    async def start_cleanup_task(self):
        """Start background task to cleanup old sessions."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Started monitoring cleanup task")
    
    async def _cleanup_loop(self):
        """Background task to clean up old sessions."""
        while True:
            try:
                await asyncio.sleep(60)  # Run every minute
                self._cleanup_old_sessions()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
    
    def _cleanup_old_sessions(self):
        """Remove sessions that ended more than retention_seconds ago."""
        cutoff = datetime.utcnow() - timedelta(seconds=self.retention_seconds)
        
        sessions_to_remove = []
        for session_id, metadata in self._sessions.items():
            if metadata.get("ended") and metadata.get("ended_at", datetime.utcnow()) < cutoff:
                sessions_to_remove.append(session_id)
        
        for session_id in sessions_to_remove:
            del self._events[session_id]
            del self._sessions[session_id]
            logger.debug(f"Cleaned up session {session_id}")
        
        if sessions_to_remove:
            logger.info(f"Cleaned up {len(sessions_to_remove)} old sessions")
    
    def clear_session(self, session_id: str):
        """Manually clear a session."""
        if session_id in self._events:
            del self._events[session_id]
        if session_id in self._sessions:
            del self._sessions[session_id]
        logger.info(f"Cleared session {session_id}")


# Global collector instance
_collector: Optional[MonitoringCollector] = None


def get_collector() -> MonitoringCollector:
    """Get global collector instance."""
    global _collector
    if _collector is None:
        _collector = MonitoringCollector()
    return _collector


def emit_event(
    session_id: str,
    category: str,
    event: str,
    metadata: Dict = None,
    latency_ms: float = None,
    severity: str = "info",
    tags: List[str] = None
) -> None:
    """
    Convenience function to emit an event.
    
    Args:
        session_id: Session ID
        category: Event category
        event: Event name
        metadata: Event metadata
        latency_ms: Optional latency
        severity: Event severity
        tags: Optional tags
    """
    collector = get_collector()
    
    event_obj = MonitoringEvent(
        session_id=session_id,
        category=category,
        event=event,
        metadata=metadata or {},
        latency_ms=latency_ms,
        severity=severity,
        tags=tags or []
    )
    
    collector.emit(event_obj)