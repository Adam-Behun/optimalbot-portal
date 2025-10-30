import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class AuditLogger:
    """HIPAA-compliant audit logging for authentication events"""

    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[os.getenv("MONGO_DB_NAME", "alfons")]
        self.audit_logs = self.db.audit_logs

    async def log_event(
        self,
        event_type: str,
        user_id: Optional[str],
        email: str,
        ip_address: str,
        user_agent: str,
        success: bool,
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Log an authentication event for HIPAA compliance.

        Args:
            event_type: Type of event (login, logout, signup, password_change, account_locked, etc.)
            user_id: User ID (None for failed login attempts)
            email: User email
            ip_address: Client IP address
            user_agent: Client user agent string
            success: Whether the operation succeeded
            details: Additional event details

        Returns:
            bool: True if logged successfully
        """
        try:
            log_entry = {
                "event_type": event_type,
                "user_id": user_id,
                "email": email,
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

    async def get_user_audit_trail(
        self,
        user_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get audit trail for a specific user"""
        try:
            cursor = self.audit_logs.find(
                {"user_id": user_id}
            ).sort("timestamp", -1).limit(limit)

            logs = await cursor.to_list(length=limit)
            return logs

        except Exception as e:
            logger.error(f"Error fetching audit trail for user {user_id}: {e}")
            return []

    async def get_failed_login_attempts(
        self,
        email: str,
        time_window_minutes: int = 30
    ) -> int:
        """
        Count failed login attempts for an email within a time window.
        Used for account lockout logic.

        Args:
            email: User email
            time_window_minutes: Time window to check (default 30 minutes)

        Returns:
            int: Number of failed attempts
        """
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
        self,
        event_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent audit events, optionally filtered by type"""
        try:
            query = {"event_type": event_type} if event_type else {}
            cursor = self.audit_logs.find(query).sort("timestamp", -1).limit(limit)

            logs = await cursor.to_list(length=limit)
            return logs

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
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Log PHI access for HIPAA compliance.

        Args:
            user_id: User ID accessing the PHI
            action: Action performed (view, create, update, delete, export)
            resource_type: Type of resource (patient, transcript, call)
            resource_id: ID of the resource accessed
            ip_address: Client IP address
            user_agent: Client user agent string
            endpoint: API endpoint accessed
            success: Whether the operation succeeded
            details: Additional event details

        Returns:
            bool: True if logged successfully
        """
        try:
            log_entry = {
                "event_type": "phi_access",
                "user_id": user_id,
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
            logger.debug(f"PHI access log: {action} {resource_type}/{resource_id} by user {user_id}")
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
        """
        Log API access for audit trail.

        Args:
            user_id: User ID making the request
            email: User email
            endpoint: API endpoint accessed
            method: HTTP method (GET, POST, etc.)
            ip_address: Client IP address
            user_agent: Client user agent string
            success: Whether the request succeeded
            details: Additional details (status code, error message, etc.)

        Returns:
            bool: True if logged successfully
        """
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
        """Create indexes for efficient querying and TTL for HIPAA retention"""
        try:
            # Create compound index for user audit trail queries
            await self.audit_logs.create_index([("user_id", 1), ("timestamp", -1)])

            # Create index for event type queries
            await self.audit_logs.create_index("event_type")

            # Create index for PHI access queries
            await self.audit_logs.create_index([("resource_type", 1), ("resource_id", 1)])

            # Create TTL index - logs expire after 6 years (HIPAA minimum retention)
            await self.audit_logs.create_index(
                "timestamp",
                expireAfterSeconds=6 * 365 * 24 * 60 * 60  # 6 years in seconds
            )

            logger.info("Audit log indexes created successfully")
            return True

        except Exception as e:
            logger.warning(f"Error creating audit indexes: {e}")
            return False


# Singleton pattern for audit logger
_audit_logger_instance: Optional[AuditLogger] = None

def get_audit_logger(db_client: Optional[AsyncIOMotorClient] = None) -> AuditLogger:
    """Get or create audit logger instance"""
    global _audit_logger_instance

    if not _audit_logger_instance:
        if not db_client:
            from backend.models import _async_client
            db_client = _async_client

            # Initialize client if not already done
            if not db_client:
                from motor.motor_asyncio import AsyncIOMotorClient
                import os
                mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
                db_client = AsyncIOMotorClient(mongo_uri)

        _audit_logger_instance = AuditLogger(db_client)

    return _audit_logger_instance
