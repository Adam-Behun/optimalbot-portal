"""
Cost aggregation service.
Handles cost queries and builds transparent breakdowns for sessions.
"""

import statistics
from dataclasses import dataclass
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.models.organization import AsyncOrganizationRecord
from costs.calculator import CostCalculator


async def _fetch_rate_docs(collection, match_filter: dict) -> list[dict]:
    """Run per-minute rate projection on a collection and return docs."""
    pipeline = [
        {"$match": match_filter},
        {
            "$project": {
                "minutes": {"$divide": ["$usage.telephony.seconds", 60]},
                "llm_usd": {"$ifNull": ["$costs.llm_usd", 0]},
                "tts_usd": {"$ifNull": ["$costs.tts_usd", 0]},
                "stt_usd": {"$ifNull": ["$costs.stt_usd", 0]},
                "telephony_usd": {"$ifNull": ["$costs.telephony_usd", 0]},
                "transfer_usd": {"$ifNull": ["$costs.transfer_usd", 0]},
            }
        },
        {
            "$project": {
                "llm_per_min": {"$divide": ["$llm_usd", "$minutes"]},
                "tts_per_min": {"$divide": ["$tts_usd", "$minutes"]},
                "stt_per_min": {"$divide": ["$stt_usd", "$minutes"]},
                "telephony_per_min": {"$divide": ["$telephony_usd", "$minutes"]},
                "transfer_per_min": {"$divide": ["$transfer_usd", "$minutes"]},
            }
        },
    ]
    return await collection.aggregate(pipeline).to_list(length=None)


async def get_benchmarks(db: AsyncIOMotorDatabase) -> dict | None:
    """
    Query sessions (live data) + cost_benchmarks (archived dev data)
    to compute per-minute rates (P90 and average) for each cost component.

    LLM/TTS/STT/telephony/transfer: derived from historical per-minute rates.
    Hosting/recording: fixed YAML rates (same every minute).
    """
    match_filter = {
        "total_cost_usd": {"$gt": 0},
        "usage.telephony.seconds": {"$gt": 10},
    }

    docs = await _fetch_rate_docs(db.sessions, match_filter)

    # Also pull from cost_benchmarks if it exists
    existing = await db.list_collection_names()
    if "cost_benchmarks" in existing:
        docs.extend(await _fetch_rate_docs(db.cost_benchmarks, match_filter))

    if not docs:
        return None

    # Session-derived rates (vary per call)
    variable_components = ["llm", "tts", "stt", "telephony", "transfer"]
    rates_by_comp = {comp: [d[f"{comp}_per_min"] for d in docs] for comp in variable_components}

    p90 = {}
    avg = {}
    for comp in variable_components:
        rates = rates_by_comp[comp]
        avg[comp] = statistics.mean(rates)
        p90[comp] = statistics.quantiles(rates, n=10)[8] if len(rates) >= 2 else rates[0]

    # Fixed-rate components (same rate every minute, no P90/avg distinction)
    calculator = CostCalculator()
    hosting_rate = calculator._get_service_rate("hosting", "pipecat_cloud", "per_minute")
    recording_rates = calculator._rates.get("recording", {}).get("daily", {})
    recording_rate = (
        recording_rates.get("audio", {}).get("per_minute", 0)
        + recording_rates.get("storage", {}).get("per_minute", 0)
    )
    for comp, rate in [("hosting", hosting_rate), ("recording", recording_rate)]:
        p90[comp] = rate
        avg[comp] = rate

    return {
        "session_count": len(docs),
        "p90": p90,
        "avg": avg,
    }


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

    @staticmethod
    def estimate_monthly_cost(minutes: float, benchmarks: dict) -> dict:
        """Multiply per-minute rates by requested minutes for each component."""
        components = ["llm", "tts", "stt", "telephony", "hosting", "recording", "transfer"]

        def build_tier(tier_key: str) -> dict:
            rates = benchmarks[tier_key]
            result = {}
            for comp in components:
                rate = rates.get(comp, 0)
                result[comp] = {
                    "per_minute": round(rate, 6),
                    "monthly_cost": round(rate * minutes, 2),
                }
            result["total"] = round(
                sum(result[c]["monthly_cost"] for c in components), 2
            )
            return result

        return {
            "monthly_minutes": minutes,
            "p90": build_tier("p90"),
            "avg": build_tier("avg"),
            "session_count": benchmarks["session_count"],
        }

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

        # Hosting breakdown
        hosting_usage = usage.get("hosting", {})
        hosting_seconds = hosting_usage.get("seconds", 0)
        if hosting_seconds > 0:
            result = self._calculator.calculate_hosting_cost(hosting_seconds)
            breakdown.append(
                CostBreakdownItem(
                    service="hosting (pipecat cloud)",
                    usage=f"{hosting_seconds:.1f}s",
                    rate=result.rate_unit,
                    formula=result.formula,
                    cost_usd=result.cost_usd,
                )
            )

        # Recording breakdown
        recording_usage = usage.get("recording", {})
        recording_seconds = recording_usage.get("seconds", 0)
        if recording_seconds > 0:
            result = self._calculator.calculate_recording_cost(recording_seconds)
            breakdown.append(
                CostBreakdownItem(
                    service="recording (daily)",
                    usage=f"{recording_seconds:.1f}s",
                    rate=result.rate_unit,
                    formula=result.formula,
                    cost_usd=result.cost_usd,
                )
            )

        # Transfer breakdown
        transfer_usage = usage.get("transfer", {})
        transfer_count = transfer_usage.get("count", 0)
        if transfer_count > 0:
            result = self._calculator.calculate_transfer_cost(transfer_count)
            breakdown.append(
                CostBreakdownItem(
                    service="transfer (sip refer)",
                    usage=f"{transfer_count} event(s)",
                    rate=result.rate_unit,
                    formula=result.formula,
                    cost_usd=result.cost_usd,
                )
            )

        return breakdown


def get_cost_service() -> CostService:
    """FastAPI dependency that provides a CostService instance."""
    return CostService()
