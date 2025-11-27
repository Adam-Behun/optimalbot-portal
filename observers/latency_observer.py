"""
Latency observer for measuring user-to-bot response time.

This observer tracks the time between when a user stops speaking and when
the bot starts responding. Metrics are:
1. Logged to console (always)
2. Sent to Langfuse via OpenTelemetry spans (when tracing is enabled)
"""

import time
from statistics import mean
from typing import List

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection

# OpenTelemetry imports - optional, gracefully degrade if not available
try:
    from opentelemetry import trace
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None


class LangfuseLatencyObserver(BaseObserver):
    """Observer that measures user-to-bot latency and reports to both console and Langfuse.

    This observer tracks:
    - Individual turn latencies (time from user stop speaking to bot start speaking)
    - Session summary statistics (avg, min, max latency)

    Metrics are always logged to console and sent to Langfuse via OpenTelemetry
    when tracing is enabled.
    """

    def __init__(self, session_id: str, patient_id: str):
        """Initialize the latency observer.

        Args:
            session_id: Unique identifier for the call session
            patient_id: Unique identifier for the patient
        """
        super().__init__()
        self._session_id = session_id
        self._patient_id = patient_id
        self._processed_frames: set = set()
        self._user_stopped_time: float = 0
        self._latencies: List[float] = []
        self._turn_count: int = 0

        # Initialize OpenTelemetry tracer if available
        if OTEL_AVAILABLE:
            self._tracer = trace.get_tracer("healthcare-voice-ai.latency")
        else:
            self._tracer = None
            logger.warning("OpenTelemetry not available - latency metrics will only be logged to console")

    async def on_push_frame(self, data: FramePushed):
        """Process frames to track speech timing and calculate latency.

        Args:
            data: Frame push event containing the frame and direction information.
        """
        # Only process downstream frames
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        # Skip already processed frames (frames pass through multiple processors)
        if data.frame.id in self._processed_frames:
            return

        self._processed_frames.add(data.frame.id)

        if isinstance(data.frame, UserStartedSpeakingFrame):
            # Reset timer when user starts speaking (new turn beginning)
            self._user_stopped_time = 0

        elif isinstance(data.frame, UserStoppedSpeakingFrame):
            # Record when user stops speaking
            self._user_stopped_time = time.time()

        elif isinstance(data.frame, (EndFrame, CancelFrame)):
            # Call ending - record final summary metrics
            self._record_final_metrics()

        elif isinstance(data.frame, BotStartedSpeakingFrame) and self._user_stopped_time:
            # Bot started responding - calculate latency
            latency = time.time() - self._user_stopped_time
            self._user_stopped_time = 0
            self._latencies.append(latency)
            self._turn_count += 1
            self._record_turn_latency(latency)

    def _record_turn_latency(self, latency: float):
        """Record individual turn latency to console and Langfuse.

        Args:
            latency: Time in seconds from user stop speaking to bot start speaking
        """
        # Always log to console
        logger.info(
            f"[Latency] Turn {self._turn_count} | "
            f"User->Bot: {latency:.3f}s | "
            f"Session: {self._session_id}"
        )

        # Send to Langfuse via OpenTelemetry if available
        if self._tracer:
            with self._tracer.start_as_current_span("latency.turn") as span:
                span.set_attribute("latency.user_to_bot_seconds", latency)
                span.set_attribute("latency.turn_number", self._turn_count)
                span.set_attribute("langfuse.session.id", self._session_id)
                span.set_attribute("patient.id", self._patient_id)
                span.set_attribute("langfuse.observation.type", "event")

    def _record_final_metrics(self):
        """Record session summary metrics to console and Langfuse."""
        if not self._latencies:
            logger.info(f"[Latency] Session {self._session_id} - No latency data recorded")
            return

        avg_latency = mean(self._latencies)
        min_latency = min(self._latencies)
        max_latency = max(self._latencies)
        total_turns = len(self._latencies)

        # Always log summary to console
        logger.info(
            f"[Latency Summary] Session: {self._session_id} | "
            f"Avg: {avg_latency:.3f}s | "
            f"Min: {min_latency:.3f}s | "
            f"Max: {max_latency:.3f}s | "
            f"Turns: {total_turns}"
        )

        # Send summary to Langfuse via OpenTelemetry if available
        if self._tracer:
            with self._tracer.start_as_current_span("latency.summary") as span:
                span.set_attribute("latency.avg_seconds", avg_latency)
                span.set_attribute("latency.min_seconds", min_latency)
                span.set_attribute("latency.max_seconds", max_latency)
                span.set_attribute("latency.turn_count", total_turns)
                span.set_attribute("langfuse.session.id", self._session_id)
                span.set_attribute("patient.id", self._patient_id)
                span.set_attribute("langfuse.observation.type", "event")

                # Also add all individual latencies as a JSON array for detailed analysis
                span.set_attribute("latency.all_turns_seconds", str(self._latencies))

    def get_metrics(self) -> dict:
        """Get current latency metrics as a dictionary.

        Returns:
            Dictionary containing latency statistics
        """
        if not self._latencies:
            return {
                "turn_count": 0,
                "avg_latency": None,
                "min_latency": None,
                "max_latency": None,
                "all_latencies": []
            }

        return {
            "turn_count": len(self._latencies),
            "avg_latency": mean(self._latencies),
            "min_latency": min(self._latencies),
            "max_latency": max(self._latencies),
            "all_latencies": self._latencies.copy()
        }
