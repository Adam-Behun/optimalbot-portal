import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

# Langfuse configuration for trace links
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
LANGFUSE_PROJECT_ID = os.getenv("LANGFUSE_PROJECT_ID", "")

from backend.dependencies import (
    get_organization_db,
    get_session_db,
    require_super_admin,
)
from backend.models.organization import AsyncOrganizationRecord
from backend.sessions import AsyncSessionRecord

router = APIRouter()


async def _build_org_map(org_db: AsyncOrganizationRecord) -> dict[str, str]:
    """Build org_id -> org_name mapping for display."""
    orgs = await org_db.list_all()
    return {str(org["_id"]): org.get("name", "Unknown") for org in orgs}


@router.get("/dashboard")
async def get_admin_dashboard(
    current_user: dict = Depends(require_super_admin),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
):
    """Get admin dashboard metrics: calls today, success rate, cost today, recent failures."""
    try:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        # Get all sessions from today (no org filter for super admin)
        pipeline = [
            {"$match": {"created_at": {"$gte": today_start}}},
            {"$group": {
                "_id": None,
                "total_calls": {"$sum": 1},
                "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
                "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
                "total_cost": {"$sum": {"$ifNull": ["$total_cost_usd", 0]}},
            }}
        ]

        result = await session_db.sessions.aggregate(pipeline).to_list(length=1)
        metrics = result[0] if result else {"total_calls": 0, "completed": 0, "failed": 0, "total_cost": 0}

        total = metrics.get("total_calls", 0)
        completed = metrics.get("completed", 0)
        success_rate = (completed / total * 100) if total > 0 else 0

        # Get recent failures (last 10)
        failures_cursor = session_db.sessions.find(
            {"status": "failed"}
        ).sort("created_at", -1).limit(10)
        recent_failures = await failures_cursor.to_list(length=10)

        org_map = await _build_org_map(org_db)

        failures_with_org = []
        for f in recent_failures:
            org_id = str(f.get("organization_id", ""))
            failures_with_org.append({
                "session_id": f.get("session_id"),
                "organization_name": org_map.get(org_id, "Unknown"),
                "workflow": f.get("workflow"),
                "created_at": f.get("created_at").isoformat() if f.get("created_at") else None,
                "error": f.get("error_message") or f.get("status"),
            })

        return {
            "calls_today": total,
            "success_rate": round(success_rate, 1),
            "cost_today_usd": round(metrics.get("total_cost", 0), 4),
            "recent_failures": failures_with_org,
        }

    except Exception:
        logger.exception("Error fetching admin dashboard")
        raise


@router.get("/calls")
async def get_admin_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    organization_id: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    current_user: dict = Depends(require_super_admin),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
):
    """Get paginated list of all calls with filters."""
    try:
        query = {}

        if organization_id:
            query["organization_id"] = ObjectId(organization_id)

        if status:
            query["status"] = status

        if date_from:
            query.setdefault("created_at", {})["$gte"] = datetime.fromisoformat(date_from.replace("Z", "+00:00"))

        if date_to:
            query.setdefault("created_at", {})["$lte"] = datetime.fromisoformat(date_to.replace("Z", "+00:00"))

        if search:
            query["$or"] = [
                {"session_id": {"$regex": search, "$options": "i"}},
                {"caller_phone": {"$regex": search, "$options": "i"}},
                {"called_phone": {"$regex": search, "$options": "i"}},
            ]

        total_count = await session_db.sessions.count_documents(query)
        total_pages = (total_count + page_size - 1) // page_size

        skip = (page - 1) * page_size
        cursor = session_db.sessions.find(query).sort("created_at", -1).skip(skip).limit(page_size)
        sessions = await cursor.to_list(length=page_size)

        org_map = await _build_org_map(org_db)

        calls = []
        for s in sessions:
            org_id = str(s.get("organization_id", ""))
            calls.append({
                "session_id": s.get("session_id"),
                "organization_id": org_id,
                "organization_name": org_map.get(org_id, "Unknown"),
                "workflow": s.get("workflow"),
                "status": s.get("status"),
                "caller_phone": s.get("caller_phone"),
                "called_phone": s.get("called_phone"),
                "total_cost_usd": s.get("total_cost_usd"),
                "created_at": s.get("created_at").isoformat() if s.get("created_at") else None,
                "completed_at": s.get("completed_at").isoformat() if s.get("completed_at") else None,
            })

        return {
            "calls": calls,
            "total_count": total_count,
            "total_pages": total_pages,
            "page": page,
            "page_size": page_size,
        }

    except Exception:
        logger.exception("Error fetching admin calls")
        raise


@router.get("/calls/{session_id}")
async def get_admin_call_detail(
    session_id: str,
    current_user: dict = Depends(require_super_admin),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
):
    """Get detailed call info including costs, transcript, and Langfuse link."""
    try:
        # Find session without org filter (super admin can see all)
        session = await session_db.sessions.find_one({"session_id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        org_map = await _build_org_map(org_db)
        org_id = str(session.get("organization_id", ""))

        # Extract cost breakdown from usage field if available
        costs_breakdown = []
        usage = session.get("usage", {})
        costs = session.get("costs", {})

        # LLM breakdown (may have per-model details)
        llm_usage = usage.get("llm", {})
        llm_models = llm_usage.get("models", {})
        if llm_models:
            # Per-model breakdown available
            for model_name, model_data in llm_models.items():
                costs_breakdown.append({
                    "model": model_name,
                    "input_tokens": model_data.get("prompt_tokens", 0),
                    "output_tokens": model_data.get("completion_tokens", 0),
                    "cost_usd": model_data.get("cost_usd", 0),
                })
        else:
            # Fallback to aggregate LLM totals
            costs_breakdown.append({
                "model": "llm",
                "input_tokens": llm_usage.get("prompt_tokens", 0),
                "output_tokens": llm_usage.get("completion_tokens", 0),
                "cost_usd": costs.get("llm_usd", 0),
            })

        # TTS breakdown
        tts_usage = usage.get("tts", {})
        costs_breakdown.append({
            "model": f"tts ({tts_usage.get('provider', 'unknown')})",
            "input_tokens": tts_usage.get("characters", 0),
            "output_tokens": 0,
            "cost_usd": costs.get("tts_usd", 0),
        })

        # STT breakdown
        stt_usage = usage.get("stt", {})
        costs_breakdown.append({
            "model": f"stt ({stt_usage.get('provider', 'unknown')})",
            "input_tokens": int(stt_usage.get("seconds", 0)),
            "output_tokens": 0,
            "cost_usd": costs.get("stt_usd", 0),
        })

        # Telephony breakdown
        telephony_usage = usage.get("telephony", {})
        costs_breakdown.append({
            "model": f"telephony ({telephony_usage.get('provider', 'unknown')})",
            "input_tokens": int(telephony_usage.get("seconds", 0)),
            "output_tokens": 0,
            "cost_usd": costs.get("telephony_usd", 0),
        })

        # Build Langfuse URL (links to session view which shows all traces for this call)
        if LANGFUSE_PROJECT_ID:
            langfuse_url = f"{LANGFUSE_HOST}/project/{LANGFUSE_PROJECT_ID}/sessions/{session_id}"
        else:
            langfuse_url = None

        return {
            "session_id": session_id,
            "organization_id": org_id,
            "organization_name": org_map.get(org_id, "Unknown"),
            "workflow": session.get("workflow"),
            "status": session.get("status"),
            "caller_phone": session.get("caller_phone"),
            "called_phone": session.get("called_phone"),
            "patient_id": session.get("patient_id"),
            "identity_verified": session.get("identity_verified"),
            "caller_name": session.get("caller_name"),
            "call_type": session.get("call_type"),
            "call_reason": session.get("call_reason"),
            "routed_to": session.get("routed_to"),
            "total_cost_usd": session.get("total_cost_usd"),
            "costs_breakdown": costs_breakdown,
            "call_transcript": session.get("call_transcript"),
            "error_message": session.get("error_message"),
            "langfuse_url": langfuse_url,
            "created_at": session.get("created_at").isoformat() if session.get("created_at") else None,
            "completed_at": session.get("completed_at").isoformat() if session.get("completed_at") else None,
            "updated_at": session.get("updated_at").isoformat() if session.get("updated_at") else None,
        }

    except Exception:
        logger.exception(f"Error fetching admin call detail for {session_id}")
        raise


@router.get("/costs")
async def get_admin_costs(
    breakdown_by_org: bool = Query(False),
    current_user: dict = Depends(require_super_admin),
    session_db: AsyncSessionRecord = Depends(get_session_db),
    org_db: AsyncOrganizationRecord = Depends(get_organization_db),
):
    """Get cost summary for today, this week, and this month."""
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        async def get_period_costs(start_date: datetime) -> tuple[float, int]:
            pipeline = [
                {"$match": {"created_at": {"$gte": start_date}}},
                {"$group": {
                    "_id": None,
                    "total_cost": {"$sum": {"$ifNull": ["$total_cost_usd", 0]}},
                    "call_count": {"$sum": 1},
                }}
            ]
            result = await session_db.sessions.aggregate(pipeline).to_list(length=1)
            if result:
                return result[0].get("total_cost", 0), result[0].get("call_count", 0)
            return 0, 0

        today_cost, today_calls = await get_period_costs(today_start)
        week_cost, week_calls = await get_period_costs(week_start)
        month_cost, month_calls = await get_period_costs(month_start)

        response = {
            "today": {
                "cost_usd": round(today_cost, 4),
                "call_count": today_calls,
            },
            "this_week": {
                "cost_usd": round(week_cost, 4),
                "call_count": week_calls,
            },
            "this_month": {
                "cost_usd": round(month_cost, 4),
                "call_count": month_calls,
            },
        }

        if breakdown_by_org:
            pipeline = [
                {"$match": {"created_at": {"$gte": month_start}}},
                {"$group": {
                    "_id": "$organization_id",
                    "total_cost": {"$sum": {"$ifNull": ["$total_cost_usd", 0]}},
                    "call_count": {"$sum": 1},
                }},
                {"$sort": {"total_cost": -1}}
            ]
            org_results = await session_db.sessions.aggregate(pipeline).to_list(length=None)

            org_map = await _build_org_map(org_db)

            response["by_organization"] = [
                {
                    "organization_id": str(r["_id"]) if r["_id"] else "unknown",
                    "organization_name": org_map.get(str(r["_id"]), "Unknown") if r["_id"] else "Unknown",
                    "cost_usd": round(r["total_cost"], 4),
                    "call_count": r["call_count"],
                }
                for r in org_results
            ]

        return response

    except Exception:
        logger.exception("Error fetching admin costs")
        raise
