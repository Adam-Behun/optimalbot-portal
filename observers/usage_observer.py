"""
Usage observer for tracking per-session costs.

Tracks LLM tokens, TTS characters, STT duration, and telephony duration.
Auto-detects provider/model from Pipecat's MetricsFrame.
Uses CostCalculator for all cost calculations.
"""

import time
from typing import Optional

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    MetricsFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection

from costs.calculator import SERVICE_CLASS_TO_PROVIDER, CostCalculator


class UsageObserver(BaseObserver):
    """Tracks LLM, TTS, STT, and telephony usage per session for cost calculation."""

    # Time window for content-based deduplication (seconds)
    _DEDUP_WINDOW = 0.5

    def __init__(
        self,
        session_id: str,
        tts_provider: str,
        stt_provider: str,
        telephony_provider: str,
    ):
        super().__init__()
        self._session_id = session_id
        self._calculator = CostCalculator()

        # LLM usage - dynamic from metrics (supports multiple providers per session)
        self._llm_usage: dict = {}  # {provider: {model: {prompt: N, completion: N}}}

        # TTS/STT/Telephony - single provider per session, passed at init
        self._tts_characters: int = 0
        self._tts_provider: str = tts_provider

        self._stt_seconds: float = 0.0
        self._stt_provider: str = stt_provider
        self._user_speaking_start: Optional[float] = None

        self._telephony_seconds: float = 0.0
        self._telephony_provider: str = telephony_provider
        self._call_connected_at: Optional[float] = None

        # Deduplication: track processed frame IDs to avoid duplicate metrics
        self._processed_frame_ids: set = set()

        # Content-based deduplication for metrics (handles duplicate frames with different IDs)
        # Maps content tuple -> timestamp of last occurrence
        self._recent_metrics: dict = {}

        # Prevent duplicate summary logs (multiple EndFrames can trigger _log_summary)
        self._summary_logged: bool = False

    async def on_push_frame(self, data: FramePushed):
        """Process frames to track usage."""
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        frame = data.frame

        if isinstance(frame, MetricsFrame):
            # Deduplicate: skip frames we've already processed
            if frame.id in self._processed_frame_ids:
                return
            self._processed_frame_ids.add(frame.id)
            self._process_metrics(frame)

        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking_start = time.time()

        elif isinstance(frame, UserStoppedSpeakingFrame):
            if self._user_speaking_start:
                self._stt_seconds += time.time() - self._user_speaking_start
                self._user_speaking_start = None

        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._log_summary()

    def _process_metrics(self, frame: MetricsFrame):
        """Extract LLM and TTS usage from MetricsFrame."""
        for metric in frame.data:
            if isinstance(metric, LLMUsageMetricsData):
                self._record_llm(metric)
            elif isinstance(metric, TTSUsageMetricsData):
                self._record_tts(metric)

    def _is_duplicate_metric(self, content_key: tuple) -> bool:
        """Check if this metric was seen recently (within DEDUP_WINDOW). Updates tracking."""
        now = time.time()
        if content_key in self._recent_metrics:
            if now - self._recent_metrics[content_key] < self._DEDUP_WINDOW:
                return True
        self._recent_metrics[content_key] = now
        return False

    def _record_llm(self, metric: LLMUsageMetricsData):
        """Record LLM token usage, aggregating by provider/model."""
        # Extract provider from processor name (e.g., "GroqLLMService#0" -> "groq")
        processor = metric.processor or "unknown"
        class_name = processor.split("#")[0]  # Remove instance suffix like "#0"
        provider = SERVICE_CLASS_TO_PROVIDER.get(class_name, class_name.lower())
        model = metric.model or "unknown"
        tokens = metric.value

        # Content-based deduplication: skip if we've seen identical metrics recently
        content_key = ("llm", provider, model, tokens.prompt_tokens, tokens.completion_tokens)
        if self._is_duplicate_metric(content_key):
            return

        if provider not in self._llm_usage:
            self._llm_usage[provider] = {}
        if model not in self._llm_usage[provider]:
            self._llm_usage[provider][model] = {"prompt": 0, "completion": 0}

        self._llm_usage[provider][model]["prompt"] += tokens.prompt_tokens
        self._llm_usage[provider][model]["completion"] += tokens.completion_tokens

        logger.debug(f"[Usage] LLM: {provider}/{model} +{tokens.prompt_tokens}/{tokens.completion_tokens}")

    def _record_tts(self, metric: TTSUsageMetricsData):
        """Record TTS character usage."""
        # Content-based deduplication: skip if we've seen identical metrics recently
        content_key = ("tts", metric.value)
        if self._is_duplicate_metric(content_key):
            return

        self._tts_characters += metric.value
        logger.debug(f"[Usage] TTS: +{metric.value} chars (total: {self._tts_characters})")

    def mark_call_connected(self):
        """Called by transport handler when call connects. Idempotent."""
        if self._call_connected_at is None:
            self._call_connected_at = time.time()
            logger.debug("[Usage] Call connected")

    def mark_call_ended(self):
        """Called by transport handler when call ends. Idempotent."""
        if self._call_connected_at and self._telephony_seconds == 0.0:
            self._telephony_seconds = time.time() - self._call_connected_at
            logger.debug(f"[Usage] Call ended, duration={self._telephony_seconds:.1f}s")

    def _finalize_in_progress_speech(self):
        """Finalize any in-progress speech duration (call ended while speaking)."""
        if self._user_speaking_start:
            self._stt_seconds += time.time() - self._user_speaking_start
            self._user_speaking_start = None

    def calculate_costs(self) -> dict:
        """Calculate costs from usage using CostCalculator."""
        # Finalize any in-progress speech
        self._finalize_in_progress_speech()

        # Delegate to CostCalculator for all calculations
        return self._calculator.calculate_session_costs(
            llm_usage=self._llm_usage,
            tts_provider=self._tts_provider,
            tts_characters=self._tts_characters,
            stt_provider=self._stt_provider,
            stt_seconds=self._stt_seconds,
            telephony_provider=self._telephony_provider,
            telephony_seconds=self._telephony_seconds,
        )

    def _log_summary(self):
        """Log usage summary at end of session."""
        if self._summary_logged:
            return
        self._summary_logged = True

        costs = self.calculate_costs()
        usage = costs["usage"]

        logger.info(
            f"[Usage] Session: {self._session_id} | "
            f"LLM: {usage['llm']['prompt_tokens']}/{usage['llm']['completion_tokens']} tokens | "
            f"TTS: {usage['tts']['characters']} chars | "
            f"STT: {usage['stt']['seconds']}s | "
            f"Call: {usage['telephony']['seconds']}s | "
            f"Total: ${costs['total_cost_usd']:.4f}"
        )

    def get_usage_summary(self) -> dict:
        """Returns usage + costs for session storage."""
        return self.calculate_costs()
