"""Metrics API endpoints for call analytics."""

from fastapi import APIRouter, Depends, Query
from typing import Optional

from backend.dependencies import get_current_user, get_current_user_organization_id
from backend.metrics import get_metrics_collector

router = APIRouter()


@router.get("/summary")
async def get_metrics_summary(
    period: str = Query("day", regex="^(day|week|month)$"),
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Get aggregated metrics summary for the organization.

    Args:
        period: Time period - "day", "week", or "month"
    """
    metrics = get_metrics_collector()
    summary = await metrics.get_period_summary(org_id, period)
    return summary


@router.get("/breakdown/status")
async def get_status_breakdown(
    period: str = Query("day", regex="^(day|week|month)$"),
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Get call count breakdown by status."""
    metrics = get_metrics_collector()
    breakdown = await metrics.get_status_breakdown(org_id, period)
    return {"breakdown": breakdown, "period": period}


@router.get("/breakdown/errors")
async def get_error_breakdown(
    period: str = Query("day", regex="^(day|week|month)$"),
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Get call failure breakdown by error stage (admin only)."""
    if current_user.get("role") != "admin":
        return {"breakdown": [], "period": period, "message": "Admin access required for error details"}

    metrics = get_metrics_collector()
    breakdown = await metrics.get_error_breakdown(org_id, period)
    return {"breakdown": breakdown, "period": period}


@router.get("/daily")
async def get_daily_metrics(
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Get today's metrics summary."""
    metrics = get_metrics_collector()
    summary = await metrics.get_daily_summary(org_id)
    return summary
