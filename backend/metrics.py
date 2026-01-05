"""Metrics collection for call tracking with 30-day TTL."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from bson import ObjectId
from loguru import logger

from backend.database import get_database, MONGO_DB_NAME


class MetricsCollector:
    """Collects and stores call metrics in MongoDB with 30-day TTL."""

    COLLECTION_NAME = "call_metrics"
    TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

    def __init__(self):
        self.db = get_database()
        self.metrics = self.db[self.COLLECTION_NAME]
        self._indexes_ensured = False

    async def ensure_indexes(self):
        """Create TTL index for 30-day expiration and query indexes."""
        if self._indexes_ensured:
            return

        try:
            # TTL index for automatic expiration
            await self.metrics.create_index(
                "timestamp",
                expireAfterSeconds=self.TTL_SECONDS
            )
            # Query indexes
            await self.metrics.create_index("organization_id")
            await self.metrics.create_index("session_id")
            await self.metrics.create_index([("workflow", 1), ("timestamp", -1)])
            await self.metrics.create_index([("organization_id", 1), ("timestamp", -1)])
            await self.metrics.create_index([("organization_id", 1), ("status", 1)])

            self._indexes_ensured = True
            logger.info("Metrics collection indexes ensured")
        except Exception as e:
            logger.error(f"Failed to ensure metrics indexes: {e}")

    async def record_call_start(
        self,
        session_id: str,
        organization_id: str,
        workflow: str,
        call_type: str = "dial-out",
        patient_id: str = None,
        phone_number: str = None,
    ) -> str:
        """Record call initiation. Returns metric document ID."""
        await self.ensure_indexes()

        doc = {
            "session_id": session_id,
            "organization_id": ObjectId(organization_id) if organization_id else None,
            "workflow": workflow,
            "call_type": call_type,
            "patient_id": patient_id,
            "phone_number": phone_number,
            "timestamp": datetime.now(timezone.utc),
            "started_at": datetime.now(timezone.utc),
            "ended_at": None,
            "duration_seconds": None,
            "status": "started",
            "dial_attempts": 1,
            "triage_result": None,
            "error": None,
            "error_stage": None,
        }

        try:
            result = await self.metrics.insert_one(doc)
            logger.debug(f"Recorded call start: session={session_id}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Failed to record call start: {e}")
            return None

    async def record_call_end(
        self,
        session_id: str,
        status: str,
        duration_seconds: float = None,
        triage_result: str = None,
        dial_attempts: int = None,
    ):
        """Record call completion with outcome."""
        try:
            update = {
                "$set": {
                    "ended_at": datetime.now(timezone.utc),
                    "status": status,
                }
            }
            if duration_seconds is not None:
                update["$set"]["duration_seconds"] = duration_seconds
            if triage_result:
                update["$set"]["triage_result"] = triage_result
            if dial_attempts is not None:
                update["$set"]["dial_attempts"] = dial_attempts

            result = await self.metrics.update_one(
                {"session_id": session_id},
                update
            )
            logger.debug(f"Recorded call end: session={session_id}, status={status}")
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to record call end: {e}")
            return False

    async def record_call_failure(
        self,
        session_id: str,
        error: str,
        stage: str,
    ):
        """Record call failure with error details."""
        try:
            # Try to update existing record
            result = await self.metrics.update_one(
                {"session_id": session_id},
                {
                    "$set": {
                        "ended_at": datetime.now(timezone.utc),
                        "status": "failed",
                        "error": error,
                        "error_stage": stage,
                    }
                }
            )

            # If no existing record, create one
            if result.matched_count == 0:
                await self.metrics.insert_one({
                    "session_id": session_id,
                    "timestamp": datetime.now(timezone.utc),
                    "started_at": datetime.now(timezone.utc),
                    "ended_at": datetime.now(timezone.utc),
                    "status": "failed",
                    "error": error,
                    "error_stage": stage,
                })

            logger.debug(f"Recorded call failure: session={session_id}, stage={stage}")
            return True
        except Exception as e:
            logger.error(f"Failed to record call failure: {e}")
            return False

    async def increment_dial_attempts(self, session_id: str):
        """Increment dial attempt counter."""
        try:
            await self.metrics.update_one(
                {"session_id": session_id},
                {"$inc": {"dial_attempts": 1}}
            )
        except Exception as e:
            logger.error(f"Failed to increment dial attempts: {e}")

    async def get_daily_summary(
        self,
        organization_id: str,
        date: datetime = None,
    ) -> dict:
        """Get aggregated metrics for a specific day."""
        if date is None:
            date = datetime.now(timezone.utc)

        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        pipeline = [
            {
                "$match": {
                    "organization_id": ObjectId(organization_id),
                    "timestamp": {"$gte": start_of_day, "$lt": end_of_day}
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_calls": {"$sum": 1},
                    "completed": {
                        "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                    },
                    "failed": {
                        "$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}
                    },
                    "voicemail": {
                        "$sum": {"$cond": [{"$eq": ["$status", "voicemail"]}, 1, 0]}
                    },
                    "avg_duration": {"$avg": "$duration_seconds"},
                    "total_dial_attempts": {"$sum": "$dial_attempts"},
                }
            }
        ]

        try:
            cursor = self.metrics.aggregate(pipeline)
            results = await cursor.to_list(length=1)

            if results:
                result = results[0]
                del result["_id"]
                result["date"] = start_of_day.isoformat()
                result["success_rate"] = (
                    (result["completed"] / result["total_calls"] * 100)
                    if result["total_calls"] > 0 else 0
                )
                return result

            return {
                "date": start_of_day.isoformat(),
                "total_calls": 0,
                "completed": 0,
                "failed": 0,
                "voicemail": 0,
                "avg_duration": None,
                "total_dial_attempts": 0,
                "success_rate": 0,
            }
        except Exception as e:
            logger.error(f"Failed to get daily summary: {e}")
            return {}

    async def get_period_summary(
        self,
        organization_id: str,
        period: str = "day",  # day, week, month
    ) -> dict:
        """Get aggregated metrics for a time period."""
        now = datetime.now(timezone.utc)

        if period == "day":
            start_date = now - timedelta(days=1)
        elif period == "week":
            start_date = now - timedelta(weeks=1)
        elif period == "month":
            start_date = now - timedelta(days=30)
        else:
            start_date = now - timedelta(days=1)

        pipeline = [
            {
                "$match": {
                    "organization_id": ObjectId(organization_id),
                    "timestamp": {"$gte": start_date}
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_calls": {"$sum": 1},
                    "completed": {
                        "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                    },
                    "failed": {
                        "$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}
                    },
                    "voicemail": {
                        "$sum": {"$cond": [{"$eq": ["$status", "voicemail"]}, 1, 0]}
                    },
                    "in_progress": {
                        "$sum": {"$cond": [{"$eq": ["$status", "started"]}, 1, 0]}
                    },
                    "avg_duration": {"$avg": "$duration_seconds"},
                    "total_dial_attempts": {"$sum": "$dial_attempts"},
                }
            }
        ]

        try:
            cursor = self.metrics.aggregate(pipeline)
            results = await cursor.to_list(length=1)

            if results:
                result = results[0]
                del result["_id"]
                result["period"] = period
                result["start_date"] = start_date.isoformat()
                result["end_date"] = now.isoformat()
                result["success_rate"] = (
                    (result["completed"] / result["total_calls"] * 100)
                    if result["total_calls"] > 0 else 0
                )
                return result

            return {
                "period": period,
                "start_date": start_date.isoformat(),
                "end_date": now.isoformat(),
                "total_calls": 0,
                "completed": 0,
                "failed": 0,
                "voicemail": 0,
                "in_progress": 0,
                "avg_duration": None,
                "total_dial_attempts": 0,
                "success_rate": 0,
            }
        except Exception as e:
            logger.error(f"Failed to get period summary: {e}")
            return {}

    async def get_status_breakdown(
        self,
        organization_id: str,
        period: str = "day",
    ) -> list:
        """Get call count breakdown by status."""
        now = datetime.now(timezone.utc)

        if period == "day":
            start_date = now - timedelta(days=1)
        elif period == "week":
            start_date = now - timedelta(weeks=1)
        elif period == "month":
            start_date = now - timedelta(days=30)
        else:
            start_date = now - timedelta(days=1)

        pipeline = [
            {
                "$match": {
                    "organization_id": ObjectId(organization_id),
                    "timestamp": {"$gte": start_date}
                }
            },
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ]

        try:
            cursor = self.metrics.aggregate(pipeline)
            results = await cursor.to_list(length=100)
            return [{"status": r["_id"], "count": r["count"]} for r in results]
        except Exception as e:
            logger.error(f"Failed to get status breakdown: {e}")
            return []

    async def get_error_breakdown(
        self,
        organization_id: str,
        period: str = "day",
    ) -> list:
        """Get call failure breakdown by error stage."""
        now = datetime.now(timezone.utc)

        if period == "day":
            start_date = now - timedelta(days=1)
        elif period == "week":
            start_date = now - timedelta(weeks=1)
        else:
            start_date = now - timedelta(days=30)

        pipeline = [
            {
                "$match": {
                    "organization_id": ObjectId(organization_id),
                    "timestamp": {"$gte": start_date},
                    "status": "failed"
                }
            },
            {
                "$group": {
                    "_id": "$error_stage",
                    "count": {"$sum": 1},
                    "errors": {"$push": "$error"}
                }
            },
            {"$sort": {"count": -1}}
        ]

        try:
            cursor = self.metrics.aggregate(pipeline)
            results = await cursor.to_list(length=100)
            return [
                {
                    "stage": r["_id"],
                    "count": r["count"],
                    "sample_errors": r["errors"][:3]  # First 3 errors as samples
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Failed to get error breakdown: {e}")
            return []


# Singleton instance
_metrics_instance: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get the singleton MetricsCollector instance."""
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = MetricsCollector()
    return _metrics_instance
