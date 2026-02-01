import os
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from typing import Optional

# Langfuse configuration for trace links
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
LANGFUSE_PROJECT_ID = os.getenv("LANGFUSE_PROJECT_ID", "")

from backend.costs.service import CostService, build_org_map, get_cost_service
from backend.dependencies import (
    get_organization_db,
    get_session_db,
    require_super_admin,
)
from backend.models.organization import AsyncOrganizationRecord
from backend.sessions import AsyncSessionRecord

router = APIRouter()


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

        org_map = await build_org_map(org_db)

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

        org_map = await build_org_map(org_db)

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
    cost_service: CostService = Depends(get_cost_service),
):
    """Get detailed call info including costs with transparency, transcript, and Langfuse link."""
    try:
        # Find session without org filter (super admin can see all)
        session = await session_db.sessions.find_one({"session_id": session_id})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        org_map = await build_org_map(org_db)
        org_id = str(session.get("organization_id", ""))

        # Build transparent cost breakdown using CostService
        breakdown_items = cost_service.build_session_cost_audit(session)
        costs_breakdown = [
            {
                "service": item.service,
                "usage": item.usage,
                "rate": item.rate,
                "formula": item.formula,
                "cost_usd": item.cost_usd,
            }
            for item in breakdown_items
        ]

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
