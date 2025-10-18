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
    IntentMetrics,
    LatencyThresholds,
    ConversationTurnEvent
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
        enable_console_logging: bool = True,
        latency_thresholds: Optional[LatencyThresholds] = None
    ):
        """
        Initialize monitoring collector.
        
        Args:
            max_events_per_session: Max events to keep per session (circular buffer)
            retention_seconds: How long to keep events after call ends
            enable_console_logging: Log events to console
            latency_thresholds: Custom latency thresholds
        """
        self.max_events_per_session = max_events_per_session
        self.retention_seconds = retention_seconds
        self.enable_console_logging = enable_console_logging
        self.latency_thresholds = latency_thresholds or LatencyThresholds()
        
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
        # Auto-determine severity based on latency thresholds
        if event.latency_ms and event.severity == "info":
            if event.category == "LATENCY":
                stage = event.metadata.get("stage", "")
                event.severity = self.latency_thresholds.get_severity(stage, event.latency_ms)
            elif event.category == "INTENT":
                event.severity = self.latency_thresholds.get_severity("intent", event.latency_ms)
            elif event.category == "LLM":
                if event.event == "llm_response_started" and event.latency_ms:
                    event.severity = self.latency_thresholds.get_severity("llm_ttft", event.latency_ms)
                elif event.event == "llm_response_completed" and event.latency_ms:
                    event.severity = self.latency_thresholds.get_severity("llm_total", event.latency_ms)
        
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
    
    def _format_latency(self, ms: float, severity: str = "info") -> str:
        """Format latency with color coding based on severity."""
        severity_colors = {
            "info": "\033[92m",     # Green
            "warning": "\033[93m",  # Yellow
            "error": "\033[91m",    # Red
        }
        
        color = severity_colors.get(severity, "")
        reset = "\033[0m"
        
        # Format based on magnitude
        if ms < 100:
            formatted = f"{ms:.1f}ms"
        elif ms < 1000:
            formatted = f"{ms:.0f}ms"
        else:
            formatted = f"{ms/1000:.2f}s"
        
        return f"{color}{formatted}{reset}"
    
    def _log_to_console(self, event: MonitoringEvent) -> None:
        """Format and log event to console with enhanced formatting."""
        # Category colors
        category_colors = {
            "CALL": "\033[95m",        # Magenta
            "INTENT": "\033[94m",      # Blue
            "TRANSITION": "\033[92m",  # Green
            "PROMPT": "\033[96m",      # Cyan
            "LLM": "\033[93m",         # Yellow
            "TRANSCRIPT": "\033[97m",  # White
            "LATENCY": "\033[96m",     # Cyan
            "ERROR": "\033[91m",       # Red
            "CONVERSATION": "\033[95m", # Magenta
        }
        
        # Severity emojis
        severity_emojis = {
            "info": "✅",
            "warning": "⚠️ ",
            "error": "🔴",
            "debug": "🔍"
        }
        
        color = category_colors.get(event.category, "")
        reset = "\033[0m"
        emoji = severity_emojis.get(event.severity, "•")
        
        # Format message based on category
        if event.category == "INTENT":
            latency_str = self._format_latency(event.latency_ms, event.severity) if event.latency_ms else ""
            message_preview = event.metadata.get('message', '')[:60]
            msg = f"{emoji} {color}[INTENT]{reset} {event.metadata.get('intent')} {latency_str} | \"{message_preview}...\""
        
        elif event.category == "TRANSITION":
            msg = f"{emoji} {color}[TRANSITION]{reset} {event.metadata.get('from_state')} → {event.metadata.get('to_state')} | Trigger: {event.metadata.get('trigger')}"
        
        elif event.category == "LATENCY":
            stage = event.metadata.get('stage', '').upper()
            latency_str = self._format_latency(event.latency_ms, event.severity) if event.latency_ms else ""
            msg = f"{emoji} {color}[LATENCY]{reset} {stage:15} {latency_str}"
            
            # Add threshold breach info
            if event.severity == "warning":
                msg += " ⚠️  SLOW"
            elif event.severity == "error":
                msg += " 🔴 CRITICAL"
            
            # Add cumulative if available
            if cumulative := event.metadata.get('cumulative_ms'):
                cumulative_str = self._format_latency(cumulative, "info")
                msg += f" (cumulative: {cumulative_str})"
        
        elif event.category == "LLM":
            latency_str = self._format_latency(event.latency_ms, event.severity) if event.latency_ms else ""
            model = event.metadata.get('model', 'unknown')
            if event.event == "llm_response_started":
                msg = f"{emoji} {color}[LLM TTFT]{reset} {model} {latency_str}"
            elif event.event == "llm_response_completed":
                tokens = event.metadata.get('completion_tokens', '')
                msg = f"{emoji} {color}[LLM TOTAL]{reset} {model} {latency_str} ({tokens} tokens)"
            else:
                msg = f"{emoji} {color}[LLM]{reset} {event.event} {latency_str}"
        
        elif event.category == "ERROR":
            msg = f"{emoji} {color}[ERROR]{reset} {event.metadata.get('error_type')}: {event.metadata.get('error_message')}"
        
        elif event.category == "TRANSCRIPT":
            role = event.metadata.get('role', 'unknown')
            content = event.metadata.get('content', '')[:80]
            role_emoji = "👤" if role == "user" else "🤖"
            msg = f"{role_emoji} {color}[{role.upper()}]{reset} {content}..."
        
        elif event.category == "CONVERSATION":
            turn = event.metadata.get('turn_number', '?')
            state = event.metadata.get('current_state', 'unknown')
            msg = f"{emoji} {color}[TURN {turn}]{reset} State: {state}"
        
        else:
            msg = f"{emoji} {color}[{event.category}]{reset} {event.event}"
            if event.latency_ms:
                latency_str = self._format_latency(event.latency_ms, event.severity)
                msg += f" {latency_str}"
        
        # Log at appropriate level
        if event.severity == "error":
            logger.error(msg)
        elif event.severity == "warning":
            logger.warning(msg)
        else:
            logger.info(msg)
    
    def emit_conversation_turn(
        self,
        session_id: str,
        turn_number: int,
        user_message: str,
        system_prompt: str,
        user_prompt: str,
        llm_response: str,
        current_state: str,
        **kwargs
    ) -> None:
        """
        Emit a complete conversation turn for later replay.
        
        Args:
            session_id: Session ID
            turn_number: Turn number in conversation
            user_message: What the user said
            system_prompt: System prompt used
            user_prompt: Full rendered prompt with context
            llm_response: LLM's response
            current_state: Current state machine state
            **kwargs: Additional metadata (intent, transition_triggered, etc.)
        """
        event = ConversationTurnEvent(
            session_id=session_id,
            event="conversation_turn",
            metadata={
                "turn_number": turn_number,
                "user_message": user_message,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "llm_response": llm_response,
                "current_state": current_state,
                **kwargs
            }
        )
        self.emit(event)
    
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
    
    def get_full_transcript(self, session_id: str) -> Dict:
        """
        Get complete conversation transcript with all prompts and responses.
        Ready for display in terminal or export to frontend.
        
        Args:
            session_id: Session to get transcript for
            
        Returns:
            Dict with turns, events_timeline, and metadata
        """
        if session_id not in self._events:
            return {
                "session_id": session_id,
                "metadata": {},
                "turns": [],
                "events_timeline": []
            }
        
        events = list(self._events[session_id])
        
        transcript = {
            "session_id": session_id,
            "metadata": self._sessions.get(session_id, {}),
            "turns": [],
            "events_timeline": []
        }
        
        # Get conversation turns
        conversation_events = [e for e in events if e.category == "CONVERSATION"]
        transcript["turns"] = [
            {
                "turn": e.metadata.get("turn_number"),
                "timestamp": e.timestamp.isoformat(),
                "state": e.metadata.get("current_state"),
                "user_message": e.metadata.get("user_message"),
                "intent": e.metadata.get("intent"),
                "system_prompt": e.metadata.get("system_prompt"),
                "user_prompt": e.metadata.get("user_prompt"),
                "llm_response": e.metadata.get("llm_response"),
                "transition_triggered": e.metadata.get("transition_triggered"),
                "tokens_used": e.metadata.get("tokens_used"),
            }
            for e in conversation_events
        ]
        
        # Get all events for timeline
        transcript["events_timeline"] = [
            {
                "timestamp": e.timestamp.isoformat(),
                "category": e.category,
                "event": e.event,
                "latency_ms": e.latency_ms,
                "severity": e.severity,
                "metadata": e.metadata
            }
            for e in events
        ]
        
        return transcript
    
    def print_full_transcript(self, session_id: str) -> None:
        """Print beautiful conversation transcript to terminal."""
        transcript = self.get_full_transcript(session_id)
        
        print("\n" + "="*80)
        print(f"📞 CALL TRANSCRIPT - Session: {session_id[:16]}...")
        
        metadata = transcript['metadata']
        if started_at := metadata.get('started_at'):
            print(f"Started: {started_at}")
        if ended_at := metadata.get('ended_at'):
            duration = (ended_at - metadata.get('started_at')).total_seconds()
            print(f"Ended: {ended_at} (Duration: {duration:.1f}s)")
        
        print("="*80)
        
        if not transcript["turns"]:
            print("\n⚠️  No conversation turns recorded for this session.")
            print("   Make sure you're using collector.emit_conversation_turn() to capture full turns.")
            return
        
        for turn in transcript["turns"]:
            print(f"\n{'─'*80}")
            print(f"🔄 Turn {turn['turn']} | {turn['timestamp']} | State: {turn['state']}")
            print(f"{'─'*80}")
            
            # User message
            print(f"\n👤 USER:")
            print(f"   {turn['user_message']}")
            
            # Intent
            if intent := turn.get('intent'):
                print(f"   💡 Intent: {intent}")
            
            # State transition
            if transition := turn.get('transition_triggered'):
                print(f"   🔀 Triggered: {transition}")
            
            # System prompt (truncated)
            print(f"\n🔧 SYSTEM PROMPT ({len(turn.get('system_prompt', ''))} chars):")
            system_preview = turn.get('system_prompt', '')[:150]
            print(f"   {system_preview}...")
            
            # Full user prompt to LLM (truncated)
            print(f"\n📝 FULL PROMPT TO LLM ({len(turn.get('user_prompt', ''))} chars):")
            prompt_preview = turn.get('user_prompt', '')[:200]
            print(f"   {prompt_preview}...")
            
            # LLM response
            print(f"\n🤖 ASSISTANT:")
            print(f"   {turn.get('llm_response', '')}")
            
            # Tokens
            if tokens := turn.get('tokens_used'):
                print(f"   📊 Tokens: {tokens}")
        
        print(f"\n{'='*80}")
        print(f"✅ Total turns: {len(transcript['turns'])}")
        print(f"{'='*80}\n")
    
    def get_latency_waterfall(self, session_id: str) -> List[Dict]:
        """
        Get latency breakdown as waterfall for visualization.
        Shows where time is spent in the pipeline.
        
        Args:
            session_id: Session to analyze
            
        Returns:
            List of cycles with stage breakdowns
        """
        if session_id not in self._events:
            return []
        
        events = list(self._events[session_id])
        
        # Group by request/response cycle
        waterfall = []
        current_cycle = None
        
        for event in events:
            # Start new cycle on user message
            if event.category == "TRANSCRIPT" and event.metadata.get("role") == "user":
                current_cycle = {
                    "user_message": event.metadata.get("content", ""),
                    "timestamp": event.timestamp.isoformat(),
                    "stages": [],
                    "total_ms": 0
                }
                waterfall.append(current_cycle)
            
            # Add latency stages to current cycle
            if current_cycle and event.latency_ms:
                stage_name = event.metadata.get("stage") or event.category.lower()
                current_cycle["stages"].append({
                    "name": stage_name,
                    "latency_ms": event.latency_ms,
                    "severity": event.severity,
                    "timestamp": event.timestamp.isoformat()
                })
                current_cycle["total_ms"] += event.latency_ms
        
        return waterfall
    
    def print_latency_waterfall(self, session_id: str) -> None:
        """Print beautiful waterfall visualization to terminal."""
        waterfall = self.get_latency_waterfall(session_id)
        
        if not waterfall:
            print("\n⚠️  No latency waterfall data available for this session.")
            return
        
        print("\n" + "="*80)
        print("📊 LATENCY WATERFALL")
        print("="*80)
        
        for i, cycle in enumerate(waterfall, 1):
            print(f"\n🔄 Cycle {i}: \"{cycle['user_message'][:50]}...\"")
            print(f"   Total: {self._format_latency(cycle['total_ms'], 'info')}")
            print("   " + "-"*70)
            
            for stage in cycle['stages']:
                # Calculate bar length (scale for visualization)
                bar_length = int(stage['latency_ms'] / 50)
                bar = "█" * min(bar_length, 60)
                
                # Severity emoji
                severity_emoji = {
                    "info": "✅",
                    "warning": "⚠️ ",
                    "error": "🔴"
                }
                
                latency_str = self._format_latency(stage['latency_ms'], stage['severity'])
                emoji = severity_emoji.get(stage['severity'], '•')
                
                print(f"   {emoji} {stage['name']:20} {latency_str:>15} {bar}")
        
        print("\n" + "="*80 + "\n")
    
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