"""
Cost-related API endpoints.
Provides cost summaries, rate transparency, and exports.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from loguru import logger

from backend.costs.excel_export import FinancialData, FinancialModelExporter
from backend.costs.service import CostService, build_org_map, get_cost_service
from backend.dependencies import (
    get_organization_db,
    get_session_db,
    require_super_admin,
)
from backend.models.organization import AsyncOrganizationRecord
from backend.sessions import AsyncSessionRecord

router = APIRouter()


@router.get("/costs")
async def get_admin_costs(
    current_user: dict = Depends(require_super_admin),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
):
    """Get cost summary for today, WTD, and MTD with breakdowns."""
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        async def get_period_stats(start_date: datetime) -> dict:
            pipeline = [
                {"$match": {"created_at": {"$gte": start_date}}},
                {
                    "$group": {
                        "_id": None,
                        "total_cost": {"$sum": {"$ifNull": ["$total_cost_usd", 0]}},
                        "call_count": {"$sum": 1},
                        "total_minutes": {"$sum": {"$ifNull": ["$usage.telephony.seconds", 0]}},
                    }
                },
            ]
            result = await session_db.sessions.aggregate(pipeline).to_list(length=1)
            if result:
                total_minutes = result[0].get("total_minutes", 0) / 60
                return {
                    "cost_usd": round(result[0].get("total_cost", 0), 4),
                    "call_count": result[0].get("call_count", 0),
                    "total_minutes": round(total_minutes, 2),
                }
            return {"cost_usd": 0, "call_count": 0, "total_minutes": 0}

        today_stats = await get_period_stats(today_start)
        week_stats = await get_period_stats(week_start)
        month_stats = await get_period_stats(month_start)

        # MTD breakdowns - single query using $facet
        mtd_pipeline = [
            {"$match": {"created_at": {"$gte": month_start}}},
            {
                "$facet": {
                    "by_component": [
                        {
                            "$group": {
                                "_id": None,
                                "llm": {"$sum": {"$ifNull": ["$costs.llm_usd", 0]}},
                                "tts": {"$sum": {"$ifNull": ["$costs.tts_usd", 0]}},
                                "stt": {"$sum": {"$ifNull": ["$costs.stt_usd", 0]}},
                                "telephony": {"$sum": {"$ifNull": ["$costs.telephony_usd", 0]}},
                            }
                        }
                    ],
                    "by_workflow": [
                        {
                            "$group": {
                                "_id": "$workflow",
                                "total_cost": {"$sum": {"$ifNull": ["$total_cost_usd", 0]}},
                                "call_count": {"$sum": 1},
                                "total_seconds": {"$sum": {"$ifNull": ["$usage.telephony.seconds", 0]}},
                            }
                        },
                        {"$sort": {"total_cost": -1}},
                    ],
                    "by_organization": [
                        {
                            "$group": {
                                "_id": "$organization_id",
                                "total_cost": {"$sum": {"$ifNull": ["$total_cost_usd", 0]}},
                                "call_count": {"$sum": 1},
                                "total_seconds": {"$sum": {"$ifNull": ["$usage.telephony.seconds", 0]}},
                            }
                        },
                        {"$sort": {"total_cost": -1}},
                    ],
                }
            },
        ]
        mtd_result = await session_db.sessions.aggregate(mtd_pipeline).to_list(length=1)
        mtd_data = mtd_result[0] if mtd_result else {}

        # Component breakdown
        components = mtd_data.get("by_component", [{}])[0] if mtd_data.get("by_component") else {}
        by_component = [
            {"component": "LLM", "cost_usd": round(components.get("llm", 0), 4)},
            {"component": "STT", "cost_usd": round(components.get("stt", 0), 4)},
            {"component": "TTS", "cost_usd": round(components.get("tts", 0), 4)},
            {"component": "Telephony", "cost_usd": round(components.get("telephony", 0), 4)},
        ]

        # Workflow breakdown
        by_workflow = [
            {
                "workflow": r["_id"] or "unknown",
                "cost_usd": round(r["total_cost"], 4),
                "call_count": r["call_count"],
                "total_minutes": round(r["total_seconds"] / 60, 2),
            }
            for r in mtd_data.get("by_workflow", [])
        ]

        # Organization breakdown
        org_map = await build_org_map(org_db)
        by_organization = [
            {
                "organization_id": str(r["_id"]) if r["_id"] else "unknown",
                "organization_name": org_map.get(str(r["_id"]), "Unknown") if r["_id"] else "Unknown",
                "cost_usd": round(r["total_cost"], 4),
                "call_count": r["call_count"],
                "total_minutes": round(r["total_seconds"] / 60, 2),
            }
            for r in mtd_data.get("by_organization", [])
        ]

        return {
            "today": today_stats,
            "wtd": week_stats,
            "mtd": month_stats,
            "by_component": by_component,
            "by_workflow": by_workflow,
            "by_organization": by_organization,
        }

    except Exception:
        logger.exception("Error fetching admin costs")
        raise


@router.get("/rates")
async def get_current_rates(
    current_user: dict = Depends(require_super_admin),
    cost_service: CostService = Depends(get_cost_service),
):
    """Return current rates from variable_costs.yaml for transparency."""
    return cost_service.get_rates()


@router.get("/export/costs")
async def export_costs(
    current_user: dict = Depends(require_super_admin),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
):
    """Export complete financial model with variable costs pre-filled."""
    try:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Single aggregation query for all summary data
        pipeline = [
            {"$match": {"created_at": {"$gte": month_start}}},
            {
                "$facet": {
                    "totals": [
                        {
                            "$group": {
                                "_id": None,
                                "call_count": {"$sum": 1},
                                "total_seconds": {"$sum": {"$ifNull": ["$usage.telephony.seconds", 0]}},
                                "llm_cost": {"$sum": {"$ifNull": ["$costs.llm_usd", 0]}},
                                "tts_cost": {"$sum": {"$ifNull": ["$costs.tts_usd", 0]}},
                                "stt_cost": {"$sum": {"$ifNull": ["$costs.stt_usd", 0]}},
                                "telephony_cost": {"$sum": {"$ifNull": ["$costs.telephony_usd", 0]}},
                            }
                        }
                    ],
                    "by_organization": [
                        {
                            "$group": {
                                "_id": "$organization_id",
                                "total_cost": {"$sum": {"$ifNull": ["$total_cost_usd", 0]}},
                                "call_count": {"$sum": 1},
                            }
                        },
                        {"$sort": {"total_cost": -1}},
                    ],
                }
            },
        ]
        result = await session_db.sessions.aggregate(pipeline).to_list(length=1)
        data = result[0] if result else {}

        totals = data.get("totals", [{}])[0] if data.get("totals") else {}

        # Build org map for customer COGS
        org_map = await build_org_map(org_db)
        customer_data = [
            (org_map.get(str(o["_id"]), "Unknown"), o["total_cost"], o["call_count"])
            for o in data.get("by_organization", [])
            if o["_id"]
        ]

        # Build financial data and export
        financial_data = FinancialData(
            period=now,
            call_count=totals.get("call_count", 0),
            total_minutes=totals.get("total_seconds", 0) / 60,
            llm_cost=totals.get("llm_cost", 0),
            stt_cost=totals.get("stt_cost", 0),
            tts_cost=totals.get("tts_cost", 0),
            telephony_cost=totals.get("telephony_cost", 0),
            customer_data=customer_data,
        )

        exporter = FinancialModelExporter(financial_data)
        buffer = exporter.build()

        filename = f"optimalbot_financials_{now.strftime('%Y-%m')}.xlsx"
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except Exception:
        logger.exception("Error exporting costs")
        raise
