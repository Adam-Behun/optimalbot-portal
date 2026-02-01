"""
Cost aggregation service.
Handles cost queries and builds transparent breakdowns for sessions.
"""

from dataclasses import dataclass
from typing import Optional

from costs.calculator import CostCalculator
from backend.models.organization import AsyncOrganizationRecord


async def build_org_map(org_db: AsyncOrganizationRecord) -> dict[str, str]:
    """Build org_id -> org_name mapping for display."""
    orgs = await org_db.list_all()
    return {str(org["_id"]): org.get("name", "Unknown") for org in orgs}


@dataclass
class CostBreakdownItem:
    """A single line item in a cost breakdown with transparency."""

    service: str
    usage: str
    rate: str
    formula: str
    cost_usd: float


class CostService:
    """Service for cost aggregation and transparency."""

    def __init__(self, calculator: Optional[CostCalculator] = None):
        self._calculator = calculator or CostCalculator()

    def get_rates(self) -> dict:
        """Return current rates from variable_costs.yaml."""
        return self._calculator.get_rates()

    def build_session_cost_audit(self, session: dict) -> list[CostBreakdownItem]:
        """
        Build a transparent cost breakdown for a session.
        Uses stored usage data to recalculate costs with formulas.

        Args:
            session: Session document from MongoDB

        Returns:
            List of CostBreakdownItem with full transparency
        """
        breakdown = []
        usage = session.get("usage", {})

        # LLM breakdown (may have per-model details)
        llm_usage = usage.get("llm", {})
        llm_models = llm_usage.get("models", {})

        if llm_models:
            for model_name, model_data in llm_models.items():
                provider = model_data.get("provider", "unknown")
                prompt_tokens = model_data.get("prompt_tokens", 0)
                completion_tokens = model_data.get("completion_tokens", 0)

                result = self._calculator.calculate_llm_cost(
                    provider, model_name, prompt_tokens, completion_tokens
                )

                breakdown.append(
                    CostBreakdownItem(
                        service=model_name,
                        usage=f"{prompt_tokens:,} in / {completion_tokens:,} out",
                        rate=result.rate_unit,
                        formula=result.formula,
                        cost_usd=result.cost_usd,
                    )
                )
        else:
            # Fallback to aggregate LLM totals (old format)
            costs = session.get("costs", {})
            prompt = llm_usage.get("prompt_tokens", 0)
            completion = llm_usage.get("completion_tokens", 0)
            breakdown.append(
                CostBreakdownItem(
                    service="llm",
                    usage=f"{prompt:,} in / {completion:,} out",
                    rate="(legacy)",
                    formula="(stored value)",
                    cost_usd=costs.get("llm_usd", 0),
                )
            )

        # TTS breakdown
        tts_usage = usage.get("tts", {})
        tts_provider = tts_usage.get("provider", "unknown")
        tts_chars = tts_usage.get("characters", 0)

        if tts_chars > 0 or tts_provider != "unknown":
            result = self._calculator.calculate_tts_cost(tts_provider, tts_chars)
            breakdown.append(
                CostBreakdownItem(
                    service=f"tts ({tts_provider})",
                    usage=f"{tts_chars:,} chars",
                    rate=result.rate_unit,
                    formula=result.formula,
                    cost_usd=result.cost_usd,
                )
            )

        # STT breakdown
        stt_usage = usage.get("stt", {})
        stt_provider = stt_usage.get("provider", "unknown")
        stt_seconds = stt_usage.get("seconds", 0)

        if stt_seconds > 0 or stt_provider != "unknown":
            result = self._calculator.calculate_stt_cost(stt_provider, stt_seconds)
            breakdown.append(
                CostBreakdownItem(
                    service=f"stt ({stt_provider})",
                    usage=f"{stt_seconds:.1f}s",
                    rate=result.rate_unit,
                    formula=result.formula,
                    cost_usd=result.cost_usd,
                )
            )

        # Telephony breakdown
        telephony_usage = usage.get("telephony", {})
        telephony_provider = telephony_usage.get("provider", "unknown")
        telephony_seconds = telephony_usage.get("seconds", 0)

        if telephony_seconds > 0 or telephony_provider != "unknown":
            result = self._calculator.calculate_telephony_cost(telephony_provider, telephony_seconds)
            breakdown.append(
                CostBreakdownItem(
                    service=f"telephony ({telephony_provider})",
                    usage=f"{telephony_seconds:.1f}s",
                    rate=result.rate_unit,
                    formula=result.formula,
                    cost_usd=result.cost_usd,
                )
            )

        return breakdown


def get_cost_service() -> CostService:
    """FastAPI dependency that provides a CostService instance."""
    return CostService()
