"""
Latency observer for measuring voice-to-voice response time with granular component breakdown.

Tracks per-turn latency with component timing:
- V2V (Voice-to-Voice): Time from user stopped speaking to bot started speaking
- LLM TTFB: Time to first LLM token
- TTS TTFB: Time from TTS request to first audio byte

Example output:
[Latency] Turn 1 | V2V: 1450ms | LLM TTFB: 350ms | TTS TTFB: 120ms
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
    LLMFullResponseStartFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection

# OpenTelemetry imports - optional
try:
    from opentelemetry import trace
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None


@dataclass
class TurnMetrics:
    """Metrics for a single conversation turn."""
    turn_number: int

    # Timestamps
    user_stop_time: float = 0
    transcription_time: float = 0  # When final transcript received
    llm_start_time: float = 0      # When LLM starts processing
    bot_start_time: float = 0
    bot_stop_time: float = 0

    # V2V latency (the key metric)
    v2v_latency: float = 0

    # Breakdown timing
    stt_finalization: float = 0    # user_stop → transcription
    pipeline_to_llm: float = 0     # transcription → llm_start

    # Component TTFB from Pipecat metrics
    llm_ttfb: float = 0
    tts_ttfb: float = 0

    def format_ms(self, seconds: float) -> int:
        """Format seconds as milliseconds integer."""
        return int(seconds * 1000) if seconds > 0 else 0


class LangfuseLatencyObserver(BaseObserver):
    """Observer that measures voice-to-voice latency per turn.

    V2V = time from user stopped speaking to bot started speaking

    Also captures LLM TTFB and TTS TTFB from Pipecat's built-in metrics.
    """

    def __init__(self, session_id: str, patient_id: str):
        super().__init__()
        self._session_id = session_id
        self._patient_id = patient_id
        self._processed_frames: set = set()
        self._turn_count: int = 0

        # Current turn tracking
        self._current_turn: Optional[TurnMetrics] = None
        self._all_turns: List[TurnMetrics] = []

        # Pending TTFB values (received before bot starts speaking)
        self._pending_llm_ttfb: float = 0
        self._pending_tts_ttfb: float = 0

        # OpenTelemetry tracer
        self._tracer = trace.get_tracer("healthcare-voice-ai.latency") if OTEL_AVAILABLE else None

    async def on_push_frame(self, data: FramePushed):
        """Process frames to track V2V latency."""
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        if data.frame.id in self._processed_frames:
            return
        self._processed_frames.add(data.frame.id)

        if isinstance(data.frame, UserStartedSpeakingFrame):
            # New turn starting
            self._turn_count += 1
            self._current_turn = TurnMetrics(turn_number=self._turn_count)
            self._pending_llm_ttfb = 0
            self._pending_tts_ttfb = 0

        elif isinstance(data.frame, UserStoppedSpeakingFrame):
            # User finished speaking - start V2V timer
            if self._current_turn:
                self._current_turn.user_stop_time = time.time()

        elif isinstance(data.frame, TranscriptionFrame):
            # Final transcription received - STT is done
            if self._current_turn and self._current_turn.user_stop_time > 0:
                now = time.time()
                self._current_turn.transcription_time = now
                self._current_turn.stt_finalization = now - self._current_turn.user_stop_time

        elif isinstance(data.frame, LLMFullResponseStartFrame):
            # LLM started processing
            if self._current_turn:
                now = time.time()
                self._current_turn.llm_start_time = now
                if self._current_turn.transcription_time > 0:
                    self._current_turn.pipeline_to_llm = now - self._current_turn.transcription_time

        elif isinstance(data.frame, MetricsFrame):
            # Capture TTFB metrics
            self._process_metrics(data.frame)

        elif isinstance(data.frame, BotStartedSpeakingFrame):
            # Bot started speaking - calculate V2V
            self._handle_bot_started()

        elif isinstance(data.frame, BotStoppedSpeakingFrame):
            # Bot finished speaking
            if self._current_turn:
                self._current_turn.bot_stop_time = time.time()
                self._all_turns.append(self._current_turn)

        elif isinstance(data.frame, (EndFrame, CancelFrame)):
            self._record_summary()

    def _process_metrics(self, frame: MetricsFrame):
        """Extract TTFB metrics from Pipecat's MetricsFrame."""
        for metric in frame.data:
            if not isinstance(metric, TTFBMetricsData):
                continue

            processor = metric.processor.lower()

            if "llm" in processor or "openai" in processor or "groq" in processor:
                self._pending_llm_ttfb = metric.value
            elif "tts" in processor or "cartesia" in processor or "elevenlabs" in processor:
                self._pending_tts_ttfb = metric.value

    def _handle_bot_started(self):
        """Calculate V2V when bot starts speaking."""
        if not self._current_turn or self._current_turn.user_stop_time == 0:
            return

        now = time.time()
        self._current_turn.bot_start_time = now
        self._current_turn.v2v_latency = now - self._current_turn.user_stop_time

        # Assign pending TTFB values
        self._current_turn.llm_ttfb = self._pending_llm_ttfb
        self._current_turn.tts_ttfb = self._pending_tts_ttfb

        # Log turn latency
        self._log_turn(self._current_turn)

        # Send to Langfuse
        self._send_turn_to_langfuse(self._current_turn)

    def _log_turn(self, turn: TurnMetrics):
        """Log turn latency breakdown."""
        parts = [f"V2V: {turn.format_ms(turn.v2v_latency)}ms"]

        # Breakdown: STT finalization (user_stop → transcript)
        if turn.stt_finalization > 0:
            parts.append(f"STT: {turn.format_ms(turn.stt_finalization)}ms")

        # Breakdown: Pipeline to LLM (transcript → llm_start)
        if turn.pipeline_to_llm > 0:
            parts.append(f"Pipe: {turn.format_ms(turn.pipeline_to_llm)}ms")

        if turn.llm_ttfb > 0:
            parts.append(f"LLM: {turn.format_ms(turn.llm_ttfb)}ms")
        if turn.tts_ttfb > 0:
            parts.append(f"TTS: {turn.format_ms(turn.tts_ttfb)}ms")

        # Calculate unexplained gap
        explained = turn.stt_finalization + turn.pipeline_to_llm + turn.llm_ttfb + turn.tts_ttfb
        gap = turn.v2v_latency - explained
        if gap > 0.03:  # Only show if > 30ms unexplained
            parts.append(f"Other: {turn.format_ms(gap)}ms")

        logger.info(f"[Latency] Turn {turn.turn_number} | {' | '.join(parts)}")

    def _send_turn_to_langfuse(self, turn: TurnMetrics):
        """Send turn metrics to Langfuse via OpenTelemetry."""
        if not self._tracer:
            return

        with self._tracer.start_as_current_span("latency.turn") as span:
            span.set_attribute("latency.v2v_ms", turn.format_ms(turn.v2v_latency))
            span.set_attribute("latency.turn_number", turn.turn_number)
            span.set_attribute("latency.llm_ttfb_ms", turn.format_ms(turn.llm_ttfb))
            span.set_attribute("latency.tts_ttfb_ms", turn.format_ms(turn.tts_ttfb))
            span.set_attribute("langfuse.session.id", self._session_id)
            span.set_attribute("patient.id", self._patient_id)

    def _record_summary(self):
        """Log session summary statistics."""
        if not self._all_turns:
            logger.info(f"[Latency] Session {self._session_id} - No latency data")
            return

        v2v_times = [t.v2v_latency for t in self._all_turns if t.v2v_latency > 0]
        llm_ttfb_times = [t.llm_ttfb for t in self._all_turns if t.llm_ttfb > 0]
        tts_ttfb_times = [t.tts_ttfb for t in self._all_turns if t.tts_ttfb > 0]

        if not v2v_times:
            return

        avg_v2v = mean(v2v_times)
        min_v2v = min(v2v_times)
        max_v2v = max(v2v_times)

        logger.info(
            f"[Latency Summary] Session: {self._session_id} | "
            f"V2V Avg: {int(avg_v2v * 1000)}ms | "
            f"Min: {int(min_v2v * 1000)}ms | "
            f"Max: {int(max_v2v * 1000)}ms | "
            f"Turns: {len(v2v_times)}"
        )

        # Component averages
        parts = []
        if llm_ttfb_times:
            parts.append(f"LLM TTFB: {int(mean(llm_ttfb_times) * 1000)}ms")
        if tts_ttfb_times:
            parts.append(f"TTS TTFB: {int(mean(tts_ttfb_times) * 1000)}ms")

        if parts:
            logger.info(f"[Component Averages] {' | '.join(parts)}")

        # Send to Langfuse
        if self._tracer:
            with self._tracer.start_as_current_span("latency.summary") as span:
                span.set_attribute("latency.v2v_avg_ms", int(avg_v2v * 1000))
                span.set_attribute("latency.v2v_min_ms", int(min_v2v * 1000))
                span.set_attribute("latency.v2v_max_ms", int(max_v2v * 1000))
                span.set_attribute("latency.turn_count", len(v2v_times))
                if llm_ttfb_times:
                    span.set_attribute("latency.llm_ttfb_avg_ms", int(mean(llm_ttfb_times) * 1000))
                if tts_ttfb_times:
                    span.set_attribute("latency.tts_ttfb_avg_ms", int(mean(tts_ttfb_times) * 1000))
                span.set_attribute("langfuse.session.id", self._session_id)
                span.set_attribute("patient.id", self._patient_id)

    def get_metrics(self) -> dict:
        """Get metrics as dictionary."""
        if not self._all_turns:
            return {"turn_count": 0, "v2v_avg_ms": None}

        v2v_times = [t.v2v_latency for t in self._all_turns if t.v2v_latency > 0]
        llm_ttfb_times = [t.llm_ttfb for t in self._all_turns if t.llm_ttfb > 0]
        tts_ttfb_times = [t.tts_ttfb for t in self._all_turns if t.tts_ttfb > 0]

        if not v2v_times:
            return {"turn_count": 0, "v2v_avg_ms": None}

        return {
            "turn_count": len(v2v_times),
            "v2v_avg_ms": int(mean(v2v_times) * 1000),
            "v2v_min_ms": int(min(v2v_times) * 1000),
            "v2v_max_ms": int(max(v2v_times) * 1000),
            "llm_ttfb_avg_ms": int(mean(llm_ttfb_times) * 1000) if llm_ttfb_times else None,
            "tts_ttfb_avg_ms": int(mean(tts_ttfb_times) * 1000) if tts_ttfb_times else None,
            "turns": [
                {
                    "turn": t.turn_number,
                    "v2v_ms": t.format_ms(t.v2v_latency),
                    "llm_ttfb_ms": t.format_ms(t.llm_ttfb),
                    "tts_ttfb_ms": t.format_ms(t.tts_ttfb),
                }
                for t in self._all_turns
            ]
        }
