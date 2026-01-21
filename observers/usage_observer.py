"""
Usage observer for tracking per-session costs.

Tracks LLM tokens, TTS characters, STT duration, and telephony duration.
Auto-detects provider/model from Pipecat's MetricsFrame.
"""

import time
from pathlib import Path
from typing import Optional

import yaml
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

_PRICING_CACHE: Optional[dict] = None


def load_pricing() -> dict:
    """Load pricing config from costs/variable_costs.yaml (cached)."""
    global _PRICING_CACHE
    if _PRICING_CACHE is not None:
        return _PRICING_CACHE

    pricing_path = Path(__file__).parent.parent / "costs" / "variable_costs.yaml"
    try:
        with open(pricing_path) as f:
            _PRICING_CACHE = yaml.safe_load(f)
            return _PRICING_CACHE
    except FileNotFoundError:
        logger.warning(f"Pricing config not found at {pricing_path}, using empty rates")
        return {}


class UsageObserver(BaseObserver):
    """Tracks LLM, TTS, STT, and telephony usage per session for cost calculation."""

    # Time window for content-based deduplication (seconds)
    _DEDUP_WINDOW = 0.5

    def __init__(self, session_id: str):
        super().__init__()
        self._session_id = session_id
        self._pricing = load_pricing()

        # LLM usage (aggregated by provider)
        self._llm_usage: dict = {}  # {provider: {model: {prompt: N, completion: N}}}

        # TTS usage (aggregated by provider)
        self._tts_characters: int = 0
        self._tts_provider: Optional[str] = None

        # STT duration (from user speaking frames)
        self._stt_seconds: float = 0.0
        self._user_speaking_start: Optional[float] = None

        # Telephony duration (from transport events)
        self._telephony_seconds: float = 0.0
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
        # Normalize provider: "GroqLLMService#0" -> "groq", "OpenAILLMService#0" -> "openai"
        raw_provider = metric.processor.lower() if metric.processor else "unknown"
        provider = raw_provider.replace("llmservice", "").split("#")[0]
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
        provider = "unknown"
        if metric.processor:
            raw = metric.processor.lower()
            provider = raw.replace("ttsservice", "").split("#")[0]

        # Content-based deduplication: skip if we've seen identical metrics recently
        content_key = ("tts", provider, metric.value)
        if self._is_duplicate_metric(content_key):
            return

        self._tts_characters += metric.value
        self._tts_provider = provider
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

    def _get_llm_rate(self, provider: str, model: str, rate_type: str) -> float:
        """Look up LLM rate from pricing config."""
        llm_rates = self._pricing.get("llm", {})
        provider_rates = llm_rates.get(provider, {})

        # Try exact model match first
        if model in provider_rates and isinstance(provider_rates[model], dict):
            return provider_rates[model].get(rate_type, 0)

        # Try base model match (strip date suffix like "-2024-07-18")
        base_model = model.split("-202")[0] if "-202" in model else model
        if base_model in provider_rates and isinstance(provider_rates[base_model], dict):
            return provider_rates[base_model].get(rate_type, 0)

        # Try prefix match (model starts with a known rate_model)
        for rate_model, rates in provider_rates.items():
            if isinstance(rates, dict) and model.startswith(rate_model):
                return rates.get(rate_type, 0)

        logger.warning(f"No LLM rate found for {provider}/{model}/{rate_type}")
        return 0

    def _get_tts_rate(self, provider: str) -> float:
        """Look up TTS rate from pricing config."""
        tts_rates = self._pricing.get("tts", {})
        provider_rates = tts_rates.get(provider, {})
        # Use first available model rate (skip metadata fields like last_verified)
        for model_rates in provider_rates.values():
            if isinstance(model_rates, dict) and "per_1m_characters" in model_rates:
                return model_rates["per_1m_characters"]
        return 0

    def _get_stt_rate(self) -> float:
        """Look up STT rate from pricing config (assumes Deepgram Flux)."""
        stt_rates = self._pricing.get("stt", {}).get("deepgram", {})
        return stt_rates.get("flux", {}).get("per_minute", 0)

    def _get_telephony_rate(self) -> float:
        """Look up telephony rate from pricing config."""
        telephony = self._pricing.get("telephony", {}).get("daily", {})
        return telephony.get("sip", {}).get("per_minute", 0)

    def _finalize_in_progress_speech(self):
        """Finalize any in-progress speech duration (call ended while speaking)."""
        if self._user_speaking_start:
            self._stt_seconds += time.time() - self._user_speaking_start
            self._user_speaking_start = None

    def calculate_costs(self) -> dict:
        """Calculate costs from usage and pricing config."""
        # Finalize any in-progress speech
        self._finalize_in_progress_speech()

        # LLM costs
        llm_cost = 0.0
        total_prompt = 0
        total_completion = 0
        llm_models = []

        for provider, models in self._llm_usage.items():
            for model, tokens in models.items():
                input_rate = self._get_llm_rate(provider, model, "input_per_1m_tokens")
                output_rate = self._get_llm_rate(provider, model, "output_per_1m_tokens")
                llm_cost += (tokens["prompt"] / 1_000_000) * input_rate
                llm_cost += (tokens["completion"] / 1_000_000) * output_rate
                total_prompt += tokens["prompt"]
                total_completion += tokens["completion"]
                llm_models.append(model)

        # TTS costs
        tts_provider = self._tts_provider
        if not tts_provider:
            logger.debug("TTS provider unknown, defaulting to cartesia for cost calc")
            tts_provider = "cartesia"
        tts_rate = self._get_tts_rate(tts_provider)
        tts_cost = (self._tts_characters / 1_000_000) * tts_rate

        # STT costs
        stt_rate = self._get_stt_rate()
        stt_cost = (self._stt_seconds / 60) * stt_rate

        # Telephony costs
        telephony_rate = self._get_telephony_rate()
        telephony_cost = (self._telephony_seconds / 60) * telephony_rate

        total_cost = llm_cost + tts_cost + stt_cost + telephony_cost

        return {
            "usage": {
                "llm": {
                    "prompt_tokens": total_prompt,
                    "completion_tokens": total_completion,
                    "models": llm_models,
                },
                "tts": {
                    "characters": self._tts_characters,
                    "provider": tts_provider,
                },
                "stt": {
                    "seconds": round(self._stt_seconds, 2),
                    "provider": "deepgram",
                },
                "telephony": {
                    "seconds": round(self._telephony_seconds, 2),
                    "provider": "daily",
                },
            },
            "costs": {
                "llm_usd": round(llm_cost, 6),
                "tts_usd": round(tts_cost, 6),
                "stt_usd": round(stt_cost, 6),
                "telephony_usd": round(telephony_cost, 6),
            },
            "total_cost_usd": round(total_cost, 4),
        }

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
