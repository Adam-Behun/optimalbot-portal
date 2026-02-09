"""
Cost calculator - single source of truth for cost calculations.
Loads rates from variable_costs.yaml and provides calculation functions with full transparency.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

_PRICING_CACHE: Optional[dict] = None

# Explicit mapping from service class names to provider names in variable_costs.yaml
# Only includes providers currently in use - add new ones as needed
SERVICE_CLASS_TO_PROVIDER = {
    # LLM
    "OpenAILLMService": "openai",
    "GroqLLMService": "groq",
    # TTS
    "CartesiaTTSService": "cartesia",
    # STT
    "DeepgramFluxSTTService": "deepgram",
    # Telephony
    "DailyTransport": "daily",
}


def get_provider_name(service_class_name: str) -> str:
    """
    Get the provider name for a service class.

    Args:
        service_class_name: The __name__ of the service class (e.g., "DeepgramFluxSTTService")

    Returns:
        Provider name as used in variable_costs.yaml (e.g., "deepgram")

    Raises:
        ValueError: If the service class is not in the mapping
    """
    if service_class_name in SERVICE_CLASS_TO_PROVIDER:
        return SERVICE_CLASS_TO_PROVIDER[service_class_name]

    raise ValueError(
        f"Unknown service class '{service_class_name}'. "
        f"Add it to SERVICE_CLASS_TO_PROVIDER in costs/calculator.py"
    )


def load_pricing() -> dict:
    """Load pricing config from costs/variable_costs.yaml (cached)."""
    global _PRICING_CACHE
    if _PRICING_CACHE is not None:
        return _PRICING_CACHE

    pricing_path = Path(__file__).parent / "variable_costs.yaml"
    try:
        with open(pricing_path) as f:
            _PRICING_CACHE = yaml.safe_load(f)
            return _PRICING_CACHE
    except FileNotFoundError:
        logger.warning(f"Pricing config not found at {pricing_path}, using empty rates")
        return {}


def clear_pricing_cache():
    """Clear the pricing cache (useful for testing or hot-reloading rates)."""
    global _PRICING_CACHE
    _PRICING_CACHE = None


@dataclass
class CostResult:
    """Result of a cost calculation with full transparency."""

    cost_usd: float
    usage: float
    unit: str
    rate: float
    rate_unit: str
    formula: str  # Human-readable: "45.2s ÷ 60 × $0.0077/min"


class CostCalculator:
    """
    Single source of truth for all cost calculations.
    Loads rates from variable_costs.yaml and provides calculation functions.
    """

    def __init__(self):
        self._rates = load_pricing()

    def get_rates(self) -> dict:
        """Return all rates for transparency display."""
        return self._rates

    def _get_llm_rate(self, provider: str, model: str, rate_type: str) -> float:
        """Look up LLM rate from pricing config."""
        llm_rates = self._rates.get("llm", {})
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

    def _get_service_rate(self, category: str, provider: str, rate_key: str) -> float:
        """
        Look up rate for non-LLM services (TTS, STT, telephony).

        These services typically have one model/tier per provider in variable_costs.yaml,
        so we search through all entries under the provider to find the rate.

        Example YAML structure:
            stt:
              deepgram:
                flux:
                  per_minute: 0.0077

        Args:
            category: Service category ("stt", "tts", "telephony")
            provider: Provider name ("deepgram", "cartesia", "daily")
            rate_key: Rate field name ("per_minute", "per_1m_characters")

        Returns:
            Rate value, or 0 if not found
        """
        provider_rates = self._rates.get(category, {}).get(provider, {})
        for model_rates in provider_rates.values():
            if isinstance(model_rates, dict) and rate_key in model_rates:
                return model_rates[rate_key]
        return 0

    def calculate_llm_cost(
        self, provider: str, model: str, prompt_tokens: int, completion_tokens: int
    ) -> CostResult:
        """
        Calculate LLM cost with full breakdown.

        Args:
            provider: LLM provider (e.g., "openai", "groq")
            model: Model name (e.g., "gpt-4o", "llama-3.3-70b-versatile")
            prompt_tokens: Number of input/prompt tokens
            completion_tokens: Number of output/completion tokens

        Returns:
            CostResult with cost, usage, rate, and formula
        """
        input_rate = self._get_llm_rate(provider, model, "input_per_1m_tokens")
        output_rate = self._get_llm_rate(provider, model, "output_per_1m_tokens")

        input_cost = (prompt_tokens / 1_000_000) * input_rate
        output_cost = (completion_tokens / 1_000_000) * output_rate
        total_cost = input_cost + output_cost

        # Build human-readable formula
        formula_parts = []
        if prompt_tokens > 0:
            formula_parts.append(f"({prompt_tokens:,}÷1M×${input_rate})")
        if completion_tokens > 0:
            formula_parts.append(f"({completion_tokens:,}÷1M×${output_rate})")
        formula = "+".join(formula_parts) if formula_parts else "0"

        return CostResult(
            cost_usd=round(total_cost, 6),
            usage=prompt_tokens + completion_tokens,
            unit="tokens",
            rate=input_rate,  # Primary rate for display
            rate_unit=f"${input_rate}/${output_rate} per 1M in/out",
            formula=formula,
        )

    def calculate_tts_cost(self, provider: str, characters: int) -> CostResult:
        """
        Calculate TTS cost with full breakdown.

        Args:
            provider: TTS provider (e.g., "cartesia")
            characters: Number of characters synthesized

        Returns:
            CostResult with cost, usage, rate, and formula
        """
        rate = self._get_service_rate("tts", provider, "per_1m_characters")
        cost = (characters / 1_000_000) * rate

        formula = f"{characters:,}÷1M×${rate}"

        return CostResult(
            cost_usd=round(cost, 6),
            usage=characters,
            unit="chars",
            rate=rate,
            rate_unit=f"${rate}/1M chars",
            formula=formula,
        )

    def calculate_stt_cost(self, provider: str, seconds: float) -> CostResult:
        """
        Calculate STT cost with full breakdown.

        Args:
            provider: STT provider (e.g., "deepgram")
            seconds: Duration in seconds

        Returns:
            CostResult with cost, usage, rate, and formula
        """
        rate = self._get_service_rate("stt", provider, "per_minute")
        cost = (seconds / 60) * rate

        formula = f"{seconds:.1f}s÷60×${rate}/min"

        return CostResult(
            cost_usd=round(cost, 6),
            usage=round(seconds, 2),
            unit="seconds",
            rate=rate,
            rate_unit=f"${rate}/min",
            formula=formula,
        )

    def calculate_telephony_cost(self, provider: str, seconds: float) -> CostResult:
        """
        Calculate telephony cost with full breakdown.

        Args:
            provider: Telephony provider (e.g., "daily")
            seconds: Call duration in seconds

        Returns:
            CostResult with cost, usage, rate, and formula
        """
        rate = self._get_service_rate("telephony", provider, "per_minute")
        cost = (seconds / 60) * rate

        formula = f"{seconds:.1f}s÷60×${rate}/min"

        return CostResult(
            cost_usd=round(cost, 6),
            usage=round(seconds, 2),
            unit="seconds",
            rate=rate,
            rate_unit=f"${rate}/min",
            formula=formula,
        )

    def calculate_hosting_cost(self, seconds: float) -> CostResult:
        """Calculate Pipecat Cloud hosting cost (agent-1x active minutes)."""
        rate = self._get_service_rate("hosting", "pipecat_cloud", "per_minute")
        cost = (seconds / 60) * rate

        return CostResult(
            cost_usd=round(cost, 6),
            usage=round(seconds, 2),
            unit="seconds",
            rate=rate,
            rate_unit=f"${rate}/min",
            formula=f"{seconds:.1f}s÷60×${rate}/min",
        )

    def calculate_recording_cost(self, seconds: float) -> CostResult:
        """Calculate Daily recording cost (audio capture + storage)."""
        recording_rates = self._rates.get("recording", {}).get("daily", {})
        audio_rate = recording_rates.get("audio", {}).get("per_minute", 0)
        storage_rate = recording_rates.get("storage", {}).get("per_minute", 0)
        combined_rate = audio_rate + storage_rate
        cost = (seconds / 60) * combined_rate

        return CostResult(
            cost_usd=round(cost, 6),
            usage=round(seconds, 2),
            unit="seconds",
            rate=combined_rate,
            rate_unit=f"${audio_rate}+${storage_rate}/min",
            formula=f"{seconds:.1f}s÷60×(${audio_rate}+${storage_rate})/min",
        )

    def calculate_transfer_cost(self, transfer_count: int) -> CostResult:
        """Calculate SIP Refer transfer cost ($0.20 per event)."""
        rate = self._rates.get("telephony", {}).get("daily", {}).get("sip_refer", {}).get("per_event", 0)
        cost = transfer_count * rate

        return CostResult(
            cost_usd=round(cost, 6),
            usage=transfer_count,
            unit="transfers",
            rate=rate,
            rate_unit=f"${rate}/event",
            formula=f"{transfer_count}×${rate}/event",
        )

    def calculate_session_costs(
        self,
        llm_usage: dict,
        tts_provider: str,
        tts_characters: int,
        stt_provider: str,
        stt_seconds: float,
        telephony_provider: str,
        telephony_seconds: float,
        transfer_count: int = 0,
    ) -> dict:
        """
        Calculate all costs for a session with full breakdown.

        Args:
            llm_usage: Dict of {provider: {model: {prompt: N, completion: N}}}
            tts_provider: TTS provider name
            tts_characters: Total TTS characters
            stt_provider: STT provider name
            stt_seconds: Total STT duration
            telephony_provider: Telephony provider name
            telephony_seconds: Total call duration

        Returns:
            Dict with usage, costs, and total_cost_usd
        """
        # LLM costs with per-model breakdown
        llm_cost = 0.0
        total_prompt = 0
        total_completion = 0
        models_breakdown = {}

        for provider, models in llm_usage.items():
            for model, tokens in models.items():
                result = self.calculate_llm_cost(
                    provider, model, tokens["prompt"], tokens["completion"]
                )
                llm_cost += result.cost_usd
                total_prompt += tokens["prompt"]
                total_completion += tokens["completion"]
                models_breakdown[model] = {
                    "provider": provider,
                    "prompt_tokens": tokens["prompt"],
                    "completion_tokens": tokens["completion"],
                    "cost_usd": result.cost_usd,
                    "formula": result.formula,
                    "rate_unit": result.rate_unit,
                }

        # TTS costs
        tts_result = self.calculate_tts_cost(tts_provider, tts_characters)

        # STT costs
        stt_result = self.calculate_stt_cost(stt_provider, stt_seconds)

        # Telephony costs
        telephony_result = self.calculate_telephony_cost(telephony_provider, telephony_seconds)

        # Hosting costs (billed on same duration as telephony)
        hosting_result = self.calculate_hosting_cost(telephony_seconds)

        # Recording costs (billed on same duration as telephony)
        recording_result = self.calculate_recording_cost(telephony_seconds)

        # Transfer costs (per-event)
        transfer_result = self.calculate_transfer_cost(transfer_count)

        total_cost = (
            llm_cost
            + tts_result.cost_usd
            + stt_result.cost_usd
            + telephony_result.cost_usd
            + hosting_result.cost_usd
            + recording_result.cost_usd
            + transfer_result.cost_usd
        )

        return {
            "usage": {
                "llm": {
                    "prompt_tokens": total_prompt,
                    "completion_tokens": total_completion,
                    "models": models_breakdown,
                },
                "tts": {
                    "characters": tts_characters,
                    "provider": tts_provider,
                    "formula": tts_result.formula,
                    "rate_unit": tts_result.rate_unit,
                },
                "stt": {
                    "seconds": round(stt_seconds, 2),
                    "provider": stt_provider,
                    "formula": stt_result.formula,
                    "rate_unit": stt_result.rate_unit,
                },
                "telephony": {
                    "seconds": round(telephony_seconds, 2),
                    "provider": telephony_provider,
                    "formula": telephony_result.formula,
                    "rate_unit": telephony_result.rate_unit,
                },
                "hosting": {
                    "seconds": round(telephony_seconds, 2),
                    "provider": "pipecat_cloud",
                    "formula": hosting_result.formula,
                    "rate_unit": hosting_result.rate_unit,
                },
                "recording": {
                    "seconds": round(telephony_seconds, 2),
                    "provider": "daily",
                    "formula": recording_result.formula,
                    "rate_unit": recording_result.rate_unit,
                },
                "transfer": {
                    "count": transfer_count,
                    "provider": "daily",
                    "formula": transfer_result.formula,
                    "rate_unit": transfer_result.rate_unit,
                },
            },
            "costs": {
                "llm_usd": round(llm_cost, 6),
                "tts_usd": tts_result.cost_usd,
                "stt_usd": stt_result.cost_usd,
                "telephony_usd": telephony_result.cost_usd,
                "hosting_usd": hosting_result.cost_usd,
                "recording_usd": recording_result.cost_usd,
                "transfer_usd": transfer_result.cost_usd,
            },
            "total_cost_usd": round(total_cost, 4),
        }


