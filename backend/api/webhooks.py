"""Webhook CRUD API endpoints."""

import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl

from backend.dependencies import get_current_user, get_current_user_organization_id
from backend.webhooks import get_webhook_dispatcher

router = APIRouter()

# Valid event types
VALID_EVENT_TYPES = [
    "call.started",
    "call.answered",
    "call.completed",
    "call.failed",
    "call.voicemail",
    "call.transferred",
]


class WebhookCreate(BaseModel):
    """Request body for creating a webhook."""
    name: str
    url: HttpUrl
    event_types: List[str]


class WebhookUpdate(BaseModel):
    """Request body for updating a webhook."""
    name: Optional[str] = None
    url: Optional[HttpUrl] = None
    event_types: Optional[List[str]] = None
    enabled: Optional[bool] = None


class WebhookResponse(BaseModel):
    """Response model for webhook."""
    id: str
    name: str
    url: str
    event_types: List[str]
    enabled: bool
    failure_count: int
    created_at: str
    last_success: Optional[str] = None
    last_failure: Optional[str] = None


class WebhookCreateResponse(WebhookResponse):
    """Response model for webhook creation (includes secret)."""
    secret: str


def _format_webhook(webhook: dict) -> dict:
    """Format webhook for API response."""
    return {
        "id": webhook.get("id", str(webhook.get("_id", ""))),
        "name": webhook.get("name", ""),
        "url": webhook.get("url", ""),
        "event_types": webhook.get("event_types", []),
        "enabled": webhook.get("enabled", False),
        "failure_count": webhook.get("failure_count", 0),
        "created_at": (
            webhook.get("created_at").isoformat()
            if isinstance(webhook.get("created_at"), datetime)
            else str(webhook.get("created_at", ""))
        ),
        "last_success": (
            webhook.get("last_success").isoformat()
            if isinstance(webhook.get("last_success"), datetime)
            else None
        ),
        "last_failure": (
            webhook.get("last_failure").isoformat()
            if isinstance(webhook.get("last_failure"), datetime)
            else None
        ),
    }


@router.get("")
async def list_webhooks(
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """List all webhooks for the organization ."""
    dispatcher = get_webhook_dispatcher()
    webhooks = await dispatcher.list_webhooks(org_id)

    return {
        "webhooks": [_format_webhook(w) for w in webhooks],
        "valid_event_types": VALID_EVENT_TYPES,
    }


@router.post("", response_model=WebhookCreateResponse)
async def create_webhook(
    data: WebhookCreate,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Create a new webhook endpoint .

    Returns the webhook configuration including the secret for HMAC verification.
    The secret is only shown once at creation time.
    """
    # Validate event types
    invalid = set(data.event_types) - set(VALID_EVENT_TYPES)
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event types: {invalid}. Valid types: {VALID_EVENT_TYPES}"
        )

    if not data.event_types:
        raise HTTPException(status_code=400, detail="At least one event type required")

    # Generate secret for HMAC signing
    secret = secrets.token_urlsafe(32)

    dispatcher = get_webhook_dispatcher()
    webhook_id = await dispatcher.register_webhook(
        organization_id=org_id,
        url=str(data.url),
        event_types=data.event_types,
        secret=secret,
        name=data.name
    )

    return WebhookCreateResponse(
        id=webhook_id,
        name=data.name,
        url=str(data.url),
        event_types=data.event_types,
        enabled=True,
        failure_count=0,
        created_at=datetime.now(timezone.utc).isoformat(),
        secret=secret,  # Only returned on creation
    )


@router.get("/{webhook_id}")
async def get_webhook(
    webhook_id: str,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Get a single webhook configuration ."""
    dispatcher = get_webhook_dispatcher()
    webhook = await dispatcher.get_webhook(webhook_id, org_id)

    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    return _format_webhook(webhook)


@router.put("/{webhook_id}")
async def update_webhook(
    webhook_id: str,
    data: WebhookUpdate,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Update webhook configuration ."""
    # Validate event types if provided
    if data.event_types:
        invalid = set(data.event_types) - set(VALID_EVENT_TYPES)
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event types: {invalid}"
            )

    updates = data.model_dump(exclude_none=True)
    if "url" in updates:
        updates["url"] = str(updates["url"])

    dispatcher = get_webhook_dispatcher()
    success = await dispatcher.update_webhook(webhook_id, org_id, updates)

    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found or no changes made")

    # Return updated webhook
    webhook = await dispatcher.get_webhook(webhook_id, org_id)
    return _format_webhook(webhook)


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Delete a webhook ."""
    dispatcher = get_webhook_dispatcher()
    success = await dispatcher.delete_webhook(webhook_id, org_id)

    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found")

    return {"deleted": True, "webhook_id": webhook_id}


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Send a test event to a webhook ."""
    dispatcher = get_webhook_dispatcher()
    result = await dispatcher.send_test_event(webhook_id, org_id)

    if result.get("success"):
        return {
            "success": True,
            "message": "Test webhook delivered successfully",
            "status_code": result.get("status_code")
        }
    else:
        # Don't expose internal error details to client
        return {
            "success": False,
            "message": "Test webhook failed",
            "status_code": result.get("status_code")
        }


@router.post("/{webhook_id}/reset")
async def reset_webhook_failures(
    webhook_id: str,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_current_user_organization_id),
):
    """Reset failure count and re-enable a disabled webhook ."""
    dispatcher = get_webhook_dispatcher()
    success = await dispatcher.update_webhook(
        webhook_id,
        org_id,
        {"enabled": True, "failure_count": 0}
    )

    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found")

    return {"success": True, "message": "Webhook reset and re-enabled"}
