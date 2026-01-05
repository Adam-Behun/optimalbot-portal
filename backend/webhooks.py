"""Webhook dispatcher for call events with HMAC signing."""

import json
import hashlib
import hmac
import asyncio
from datetime import datetime, timezone
from typing import Optional, List
from bson import ObjectId
import aiohttp
from loguru import logger

from backend.database import get_database


class WebhookDispatcher:
    """Dispatch webhook events to registered endpoints."""

    WEBHOOKS_COLLECTION = "webhooks"
    WEBHOOK_LOGS_COLLECTION = "webhook_logs"
    WEBHOOK_DELIVERY_TIMEOUT = 10  # seconds
    MAX_FAILURE_COUNT = 10  # Disable after this many failures
    LOG_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

    # Valid event types
    EVENT_TYPES = [
        "call.started",
        "call.answered",
        "call.completed",
        "call.failed",
        "call.voicemail",
        "call.transferred",
    ]

    def __init__(self):
        self.db = get_database()
        self.webhooks = self.db[self.WEBHOOKS_COLLECTION]
        self.webhook_logs = self.db[self.WEBHOOK_LOGS_COLLECTION]
        self._indexes_ensured = False

    async def ensure_indexes(self):
        """Create indexes for webhook collections."""
        if self._indexes_ensured:
            return

        try:
            await self.webhooks.create_index("organization_id")
            await self.webhooks.create_index([("organization_id", 1), ("event_types", 1)])

            # TTL index for log cleanup
            await self.webhook_logs.create_index(
                "timestamp",
                expireAfterSeconds=self.LOG_TTL_SECONDS
            )
            await self.webhook_logs.create_index("webhook_id")

            self._indexes_ensured = True
            logger.info("Webhook collection indexes ensured")
        except Exception as e:
            logger.error(f"Failed to ensure webhook indexes: {e}")

    async def register_webhook(
        self,
        organization_id: str,
        url: str,
        event_types: List[str],
        secret: str,
        name: str = "",
    ) -> str:
        """Register a new webhook endpoint. Returns webhook ID."""
        await self.ensure_indexes()

        doc = {
            "organization_id": ObjectId(organization_id),
            "url": url,
            "event_types": event_types,
            "secret": secret,  # For HMAC signing
            "name": name,
            "enabled": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "failure_count": 0,
            "last_failure": None,
            "last_success": None,
        }

        result = await self.webhooks.insert_one(doc)
        logger.info(f"Registered webhook {result.inserted_id} for org {organization_id}")
        return str(result.inserted_id)

    async def update_webhook(
        self,
        webhook_id: str,
        organization_id: str,
        updates: dict,
    ) -> bool:
        """Update webhook configuration."""
        allowed_fields = {"name", "url", "event_types", "enabled"}
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}

        if not filtered_updates:
            return False

        filtered_updates["updated_at"] = datetime.now(timezone.utc)

        result = await self.webhooks.update_one(
            {
                "_id": ObjectId(webhook_id),
                "organization_id": ObjectId(organization_id)
            },
            {"$set": filtered_updates}
        )
        return result.modified_count > 0

    async def delete_webhook(
        self,
        webhook_id: str,
        organization_id: str,
    ) -> bool:
        """Delete a webhook."""
        result = await self.webhooks.delete_one({
            "_id": ObjectId(webhook_id),
            "organization_id": ObjectId(organization_id)
        })
        return result.deleted_count > 0

    async def get_webhook(
        self,
        webhook_id: str,
        organization_id: str,
    ) -> Optional[dict]:
        """Get a single webhook."""
        webhook = await self.webhooks.find_one({
            "_id": ObjectId(webhook_id),
            "organization_id": ObjectId(organization_id)
        })
        if webhook:
            webhook["id"] = str(webhook.pop("_id"))
            webhook["organization_id"] = str(webhook["organization_id"])
        return webhook

    async def list_webhooks(
        self,
        organization_id: str,
    ) -> List[dict]:
        """List all webhooks for an organization."""
        cursor = self.webhooks.find({"organization_id": ObjectId(organization_id)})
        webhooks = await cursor.to_list(length=100)

        for webhook in webhooks:
            webhook["id"] = str(webhook.pop("_id"))
            webhook["organization_id"] = str(webhook["organization_id"])

        return webhooks

    async def dispatch_event(
        self,
        organization_id: str,
        event_type: str,
        payload: dict,
    ):
        """Dispatch event to all registered webhooks for the organization."""
        if event_type not in self.EVENT_TYPES:
            logger.warning(f"Unknown webhook event type: {event_type}")
            return

        await self.ensure_indexes()

        # Find all enabled webhooks that subscribe to this event
        webhooks = await self.webhooks.find({
            "organization_id": ObjectId(organization_id),
            "event_types": event_type,
            "enabled": True
        }).to_list(length=100)

        if not webhooks:
            logger.debug(f"No webhooks registered for {event_type} in org {organization_id}")
            return

        # Dispatch to all webhooks in parallel
        tasks = [
            self._send_webhook(webhook, event_type, payload)
            for webhook in webhooks
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(
        self,
        webhook: dict,
        event_type: str,
        payload: dict,
    ):
        """Send webhook with HMAC signature."""
        body = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload
        }
        body_bytes = json.dumps(body, default=str).encode()

        # HMAC signature
        signature = hmac.new(
            webhook["secret"].encode(),
            body_bytes,
            hashlib.sha256
        ).hexdigest()

        webhook_id = webhook["_id"]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook["url"],
                    data=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": f"sha256={signature}",
                        "X-Webhook-Event": event_type,
                        "X-Webhook-Timestamp": body["timestamp"],
                    },
                    timeout=aiohttp.ClientTimeout(total=self.WEBHOOK_DELIVERY_TIMEOUT)
                ) as resp:
                    success = resp.status < 400

                    await self._log_delivery(
                        webhook_id, event_type, success, resp.status
                    )

                    if success:
                        await self._record_success(webhook_id)
                        logger.debug(f"Webhook delivered: {event_type} to {webhook['url']}")
                    else:
                        await self._increment_failure(webhook_id)
                        logger.warning(f"Webhook failed: {event_type} to {webhook['url']} - HTTP {resp.status}")

        except asyncio.TimeoutError:
            await self._log_delivery(webhook_id, event_type, False, None, "timeout")
            await self._increment_failure(webhook_id)
            logger.warning(f"Webhook timeout: {event_type} to {webhook['url']}")
        except Exception as e:
            await self._log_delivery(webhook_id, event_type, False, None, str(e)[:200])
            await self._increment_failure(webhook_id)
            logger.error(f"Webhook error: {event_type} to {webhook['url']} - {e}")

    async def _log_delivery(
        self,
        webhook_id: ObjectId,
        event_type: str,
        success: bool,
        status_code: Optional[int],
        error: str = None,
    ):
        """Log webhook delivery attempt."""
        try:
            await self.webhook_logs.insert_one({
                "webhook_id": webhook_id,
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc),
                "success": success,
                "status_code": status_code,
                "error": error
            })
        except Exception as e:
            logger.error(f"Failed to log webhook delivery: {e}")

    async def _record_success(self, webhook_id: ObjectId):
        """Record successful delivery and reset failure count."""
        try:
            await self.webhooks.update_one(
                {"_id": webhook_id},
                {
                    "$set": {
                        "last_success": datetime.now(timezone.utc),
                        "failure_count": 0
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to record webhook success: {e}")

    async def _increment_failure(self, webhook_id: ObjectId):
        """Increment failure count and disable if threshold exceeded."""
        try:
            result = await self.webhooks.find_one_and_update(
                {"_id": webhook_id},
                {
                    "$inc": {"failure_count": 1},
                    "$set": {"last_failure": datetime.now(timezone.utc)}
                },
                return_document=True
            )

            # Disable after too many consecutive failures
            if result and result.get("failure_count", 0) >= self.MAX_FAILURE_COUNT:
                await self.webhooks.update_one(
                    {"_id": webhook_id},
                    {"$set": {"enabled": False}}
                )
                logger.warning(f"Webhook {webhook_id} disabled due to {self.MAX_FAILURE_COUNT} consecutive failures")
        except Exception as e:
            logger.error(f"Failed to increment webhook failure: {e}")

    async def send_test_event(
        self,
        webhook_id: str,
        organization_id: str,
    ) -> dict:
        """Send a test event to a webhook."""
        webhook = await self.webhooks.find_one({
            "_id": ObjectId(webhook_id),
            "organization_id": ObjectId(organization_id)
        })

        if not webhook:
            return {"success": False, "error": "Webhook not found"}

        test_payload = {
            "session_id": "test-session-id",
            "patient_id": "test-patient-id",
            "message": "This is a test webhook event"
        }

        try:
            body = {
                "event": "test",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": test_payload
            }
            body_bytes = json.dumps(body).encode()

            signature = hmac.new(
                webhook["secret"].encode(),
                body_bytes,
                hashlib.sha256
            ).hexdigest()

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook["url"],
                    data=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": f"sha256={signature}",
                        "X-Webhook-Event": "test",
                    },
                    timeout=aiohttp.ClientTimeout(total=self.WEBHOOK_DELIVERY_TIMEOUT)
                ) as resp:
                    if resp.status < 400:
                        return {"success": True, "status_code": resp.status}
                    else:
                        return {"success": False, "status_code": resp.status}

        except asyncio.TimeoutError:
            return {"success": False, "error": "timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Singleton instance
_dispatcher_instance: Optional[WebhookDispatcher] = None


def get_webhook_dispatcher() -> WebhookDispatcher:
    """Get the singleton WebhookDispatcher instance."""
    global _dispatcher_instance
    if _dispatcher_instance is None:
        _dispatcher_instance = WebhookDispatcher()
    return _dispatcher_instance
