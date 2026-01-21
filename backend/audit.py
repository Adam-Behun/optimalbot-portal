from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClient

from backend.database import get_mongo_client, MONGO_DB_NAME


class AuditLogger:
    def __init__(self, db_client: "AsyncIOMotorClient"):
        self.client = db_client
        self.db = db_client[MONGO_DB_NAME]
        self.audit_logs = self.db.audit_logs

    async def log_event(
        self,
        event_type: str,
        user_id: Optional[str],
        email: str,
        ip_address: str,
        user_agent: str,
        success: bool,
        details: Optional[Dict[str, Any]] = None,
        organization_id: Optional[str] = None
    ) -> bool:
        try:
            log_entry = {
                "event_type": event_type,
                "user_id": user_id,
                "email": email,
                "organization_id": organization_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "timestamp": datetime.utcnow().isoformat(),
                "success": success,
                "details": details or {}
            }
            await self.audit_logs.insert_one(log_entry)
            logger.info(f"Audit log: {event_type} for {email} - Success: {success}")
            return True
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
            return False

    async def get_user_audit_trail(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            cursor = self.audit_logs.find({"user_id": user_id}).sort("timestamp", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Error fetching audit trail for user {user_id}: {e}")
            return []

    async def get_failed_login_attempts(self, email: str, time_window_minutes: int = 30) -> int:
        try:
            from datetime import timedelta
            cutoff_time = datetime.utcnow() - timedelta(minutes=time_window_minutes)
            count = await self.audit_logs.count_documents({
                "email": email,
                "event_type": "login",
                "success": False,
                "timestamp": {"$gte": cutoff_time.isoformat()}
            })
            return count
        except Exception as e:
            logger.error(f"Error counting failed login attempts for {email}: {e}")
            return 0

    async def get_recent_events(
        self, event_type: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        try:
            query = {"event_type": event_type} if event_type else {}
            cursor = self.audit_logs.find(query).sort("timestamp", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Error fetching recent events: {e}")
            return []

    async def log_phi_access(
        self,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        ip_address: str,
        user_agent: str,
        endpoint: str,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
        organization_id: Optional[str] = None
    ) -> bool:
        try:
            log_entry = {
                "event_type": "phi_access",
                "user_id": user_id,
                "organization_id": organization_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "endpoint": endpoint,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "timestamp": datetime.utcnow().isoformat(),
                "success": success,
                "details": details or {}
            }
            await self.audit_logs.insert_one(log_entry)
            logger.trace(f"PHI access log: {action} {resource_type}/{resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to write PHI access log: {e}")
            return False

    async def log_api_access(
        self,
        user_id: str,
        email: str,
        endpoint: str,
        method: str,
        ip_address: str,
        user_agent: str,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        try:
            log_entry = {
                "event_type": "api_access",
                "user_id": user_id,
                "email": email,
                "endpoint": endpoint,
                "method": method,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "timestamp": datetime.utcnow().isoformat(),
                "success": success,
                "details": details or {}
            }
            await self.audit_logs.insert_one(log_entry)
            return True
        except Exception as e:
            logger.error(f"Failed to write API access log: {e}")
            return False

    async def ensure_indexes(self):
        try:
            await self.audit_logs.create_index([("user_id", 1), ("timestamp", -1)])
            await self.audit_logs.create_index("event_type")
            await self.audit_logs.create_index([("resource_type", 1), ("resource_id", 1)])
            await self.audit_logs.create_index("organization_id")
            # TTL index - logs expire after 6 years (HIPAA minimum retention)
            six_years_seconds = 6 * 365 * 24 * 60 * 60
            await self.audit_logs.create_index("timestamp", expireAfterSeconds=six_years_seconds)
            logger.info("Audit log indexes created successfully")
            return True
        except Exception as e:
            logger.warning(f"Error creating audit indexes: {e}")
            return False


_audit_logger_instance: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger_instance
    if _audit_logger_instance is None:
        _audit_logger_instance = AuditLogger(get_mongo_client())
    return _audit_logger_instance
