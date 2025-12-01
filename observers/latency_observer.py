"""
Latency observer for measuring user-to-bot response time with component breakdown.

This observer tracks:
1. Total user-to-bot latency per turn
2. Per-component latencies: STT, LLM (TTFB + total), TTS (TTFB + total)
3. User speech duration with timestamps

Metrics are:
1. Logged to console (always) - including detailed per-turn breakdown table
2. Sent to Langfuse via OpenTelemetry spans (when tracing is enabled)
"""

import time
from dataclasses import dataclass
from statistics import mean
from typing import List, Optional

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    MetricsFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData, ProcessingMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection

# OpenTelemetry imports - optional, gracefully degrade if not available
try:
    from opentelemetry import trace
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None


@dataclass
class TurnMetrics:
    """Detailed metrics for a single conversation turn."""
    turn_number: int
    # User speech timing
    user_start_time: float = 0
    user_stop_time: float = 0
    user_duration: float = 0
    # STT metrics
    stt_processing: float = 0
    # LLM metrics
    llm_ttfb: float = 0
    llm_processing: float = 0
    # TTS metrics
    tts_ttfb: float = 0
    tts_processing: float = 0
    # Bot speech timing
    bot_start_time: float = 0
    bot_stop_time: float = 0
    # Total latency (user stop → bot start)
    total_latency: float = 0

    def format_time(self, timestamp: float) -> str:
        """Format timestamp as HH:MM:SS.mmm"""
        if timestamp == 0:
            return "N/A"
        from datetime import datetime
        return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]


class LangfuseLatencyObserver(BaseObserver):
    """Observer that measures user-to-bot latency with component breakdown.

    This observer tracks:
    - Individual turn latencies (time from user stop speaking to bot start speaking)
    - Per-component metrics: STT processing, LLM TTFB/processing, TTS TTFB/processing
    - User speech duration with timestamps
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
        self._latencies: List[float] = []
        self._turn_count: int = 0

        # Current turn tracking
        self._current_turn: Optional[TurnMetrics] = None
        self._all_turns: List[TurnMetrics] = []

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
            # Start a new turn when user starts speaking
            self._turn_count += 1
            self._current_turn = TurnMetrics(turn_number=self._turn_count)
            self._current_turn.user_start_time = time.time()

        elif isinstance(data.frame, UserStoppedSpeakingFrame):
            # Record when user stops speaking
            if self._current_turn:
                self._current_turn.user_stop_time = time.time()
                self._current_turn.user_duration = (
                    self._current_turn.user_stop_time - self._current_turn.user_start_time
                )

        elif isinstance(data.frame, MetricsFrame):
            # Capture component metrics from MetricsFrame
            self._process_metrics_frame(data.frame)

        elif isinstance(data.frame, BotStartedSpeakingFrame):
            # Bot started responding - calculate total latency
            if self._current_turn and self._current_turn.user_stop_time:
                self._current_turn.bot_start_time = time.time()
                self._current_turn.total_latency = (
                    self._current_turn.bot_start_time - self._current_turn.user_stop_time
                )
                self._latencies.append(self._current_turn.total_latency)
                self._record_turn_latency(self._current_turn)

        elif isinstance(data.frame, BotStoppedSpeakingFrame):
            # Record when bot stops speaking
            if self._current_turn:
                self._current_turn.bot_stop_time = time.time()
                self._all_turns.append(self._current_turn)

        elif isinstance(data.frame, (EndFrame, CancelFrame)):
            # Call ending - record final summary metrics
            self._record_final_metrics()

    def _process_metrics_frame(self, frame: MetricsFrame):
        """Extract component metrics from MetricsFrame and assign to current turn."""
        if not self._current_turn:
            return

        for metric in frame.data:
            processor = metric.processor.lower()

            if isinstance(metric, TTFBMetricsData):
                if "llm" in processor or "openai" in processor or "groq" in processor:
                    self._current_turn.llm_ttfb = metric.value
                elif "tts" in processor or "cartesia" in processor or "elevenlabs" in processor:
                    self._current_turn.tts_ttfb = metric.value

            elif isinstance(metric, ProcessingMetricsData):
                if "stt" in processor or "deepgram" in processor:
                    self._current_turn.stt_processing = metric.value
                elif "llm" in processor or "openai" in processor or "groq" in processor:
                    self._current_turn.llm_processing = metric.value
                elif "tts" in processor or "cartesia" in processor or "elevenlabs" in processor:
                    self._current_turn.tts_processing = metric.value

    def _record_turn_latency(self, turn: TurnMetrics):
        """Record individual turn latency with component breakdown to console and Langfuse.

        Args:
            turn: TurnMetrics object containing all timing data for this turn
        """
        # Format detailed breakdown for console
        user_time_str = f"{turn.format_time(turn.user_start_time)}-{turn.format_time(turn.user_stop_time)}"

        # Build component breakdown string
        components = []
        if turn.user_duration > 0:
            components.append(f"User: {turn.user_duration:.2f}s ({user_time_str})")
        if turn.stt_processing > 0:
            components.append(f"STT: {turn.stt_processing:.2f}s")
        if turn.llm_ttfb > 0 or turn.llm_processing > 0:
            llm_str = f"LLM: {turn.llm_ttfb:.2f}s TTFB"
            if turn.llm_processing > 0:
                llm_str += f", {turn.llm_processing:.2f}s total"
            components.append(llm_str)
        if turn.tts_ttfb > 0:
            components.append(f"TTS: {turn.tts_ttfb:.2f}s TTFB")

        component_str = " | ".join(components) if components else "No component data"

        # Log detailed breakdown to console
        logger.info(
            f"[Latency] Turn {turn.turn_number} | "
            f"Total: {turn.total_latency:.3f}s | "
            f"{component_str}"
        )

        # Send to Langfuse via OpenTelemetry if available (with detailed attributes)
        if self._tracer:
            with self._tracer.start_as_current_span("latency.turn") as span:
                span.set_attribute("latency.user_to_bot_seconds", turn.total_latency)
                span.set_attribute("latency.turn_number", turn.turn_number)
                span.set_attribute("latency.user_duration_seconds", turn.user_duration)
                span.set_attribute("latency.stt_processing_seconds", turn.stt_processing)
                span.set_attribute("latency.llm_ttfb_seconds", turn.llm_ttfb)
                span.set_attribute("latency.llm_processing_seconds", turn.llm_processing)
                span.set_attribute("latency.tts_ttfb_seconds", turn.tts_ttfb)
                span.set_attribute("latency.tts_processing_seconds", turn.tts_processing)
                span.set_attribute("langfuse.session.id", self._session_id)
                span.set_attribute("patient.id", self._patient_id)
                span.set_attribute("langfuse.observation.type", "event")

    def _record_final_metrics(self):
        """Record session summary metrics with component breakdown to console and Langfuse."""
        if not self._latencies:
            logger.info(f"[Latency] Session {self._session_id} - No latency data recorded")
            return

        avg_latency = mean(self._latencies)
        min_latency = min(self._latencies)
        max_latency = max(self._latencies)
        total_turns = len(self._latencies)

        # Calculate component averages from turns that have data
        stt_times = [t.stt_processing for t in self._all_turns if t.stt_processing > 0]
        llm_ttfb_times = [t.llm_ttfb for t in self._all_turns if t.llm_ttfb > 0]
        llm_proc_times = [t.llm_processing for t in self._all_turns if t.llm_processing > 0]
        tts_ttfb_times = [t.tts_ttfb for t in self._all_turns if t.tts_ttfb > 0]
        user_durations = [t.user_duration for t in self._all_turns if t.user_duration > 0]

        # Build detailed summary
        logger.info(
            f"[Latency Summary] Session: {self._session_id} | "
            f"Avg: {avg_latency:.3f}s | "
            f"Min: {min_latency:.3f}s | "
            f"Max: {max_latency:.3f}s | "
            f"Turns: {total_turns}"
        )

        # Log component breakdown if we have data
        if any([stt_times, llm_ttfb_times, tts_ttfb_times]):
            breakdown = []
            if user_durations:
                breakdown.append(f"User Speech: {mean(user_durations):.2f}s avg")
            if stt_times:
                breakdown.append(f"STT: {mean(stt_times):.2f}s avg")
            if llm_ttfb_times:
                breakdown.append(f"LLM TTFB: {mean(llm_ttfb_times):.2f}s avg")
            if llm_proc_times:
                breakdown.append(f"LLM Total: {mean(llm_proc_times):.2f}s avg")
            if tts_ttfb_times:
                breakdown.append(f"TTS TTFB: {mean(tts_ttfb_times):.2f}s avg")

            logger.info(f"[Component Breakdown] {' | '.join(breakdown)}")

        # Send summary to Langfuse via OpenTelemetry if available
        if self._tracer:
            with self._tracer.start_as_current_span("latency.summary") as span:
                span.set_attribute("latency.avg_seconds", avg_latency)
                span.set_attribute("latency.min_seconds", min_latency)
                span.set_attribute("latency.max_seconds", max_latency)
                span.set_attribute("latency.turn_count", total_turns)
                # Component averages
                if stt_times:
                    span.set_attribute("latency.stt_avg_seconds", mean(stt_times))
                if llm_ttfb_times:
                    span.set_attribute("latency.llm_ttfb_avg_seconds", mean(llm_ttfb_times))
                if llm_proc_times:
                    span.set_attribute("latency.llm_processing_avg_seconds", mean(llm_proc_times))
                if tts_ttfb_times:
                    span.set_attribute("latency.tts_ttfb_avg_seconds", mean(tts_ttfb_times))
                span.set_attribute("langfuse.session.id", self._session_id)
                span.set_attribute("patient.id", self._patient_id)
                span.set_attribute("langfuse.observation.type", "event")
                span.set_attribute("latency.all_turns_seconds", str(self._latencies))

    def get_metrics(self) -> dict:
        """Get current latency metrics as a dictionary.

        Returns:
            Dictionary containing latency statistics and component breakdowns
        """
        if not self._latencies:
            return {
                "turn_count": 0,
                "avg_latency": None,
                "min_latency": None,
                "max_latency": None,
                "all_latencies": [],
                "component_breakdown": {}
            }

        # Calculate component averages
        stt_times = [t.stt_processing for t in self._all_turns if t.stt_processing > 0]
        llm_ttfb_times = [t.llm_ttfb for t in self._all_turns if t.llm_ttfb > 0]
        llm_proc_times = [t.llm_processing for t in self._all_turns if t.llm_processing > 0]
        tts_ttfb_times = [t.tts_ttfb for t in self._all_turns if t.tts_ttfb > 0]
        user_durations = [t.user_duration for t in self._all_turns if t.user_duration > 0]

        return {
            "turn_count": len(self._latencies),
            "avg_latency": mean(self._latencies),
            "min_latency": min(self._latencies),
            "max_latency": max(self._latencies),
            "all_latencies": self._latencies.copy(),
            "component_breakdown": {
                "user_speech_avg": mean(user_durations) if user_durations else None,
                "stt_avg": mean(stt_times) if stt_times else None,
                "llm_ttfb_avg": mean(llm_ttfb_times) if llm_ttfb_times else None,
                "llm_processing_avg": mean(llm_proc_times) if llm_proc_times else None,
                "tts_ttfb_avg": mean(tts_ttfb_times) if tts_ttfb_times else None,
            },
            "turns": [
                {
                    "turn": t.turn_number,
                    "total_latency": t.total_latency,
                    "user_duration": t.user_duration,
                    "stt_processing": t.stt_processing,
                    "llm_ttfb": t.llm_ttfb,
                    "llm_processing": t.llm_processing,
                    "tts_ttfb": t.tts_ttfb,
                }
                for t in self._all_turns
            ]
        }
