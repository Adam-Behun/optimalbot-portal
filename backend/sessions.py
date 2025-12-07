from datetime import datetime, timezone
from typing import Optional, List, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClient

from backend.database import get_mongo_client, MONGO_DB_NAME
from backend.constants import SessionStatus


class AsyncSessionRecord:
    def __init__(self, db_client: "AsyncIOMotorClient"):
        self.client = db_client
        self.db = db_client[MONGO_DB_NAME]
        self.sessions = self.db.sessions

    async def _ensure_indexes(self):
        try:
            from bson import ObjectId
            await self.sessions.create_index([("organization_id", 1), ("created_at", -1)])
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    async def create_session(self, session_data: dict) -> bool:
        try:
            from bson import ObjectId
            await self._ensure_indexes()

            if "organization_id" in session_data and isinstance(session_data["organization_id"], str):
                session_data["organization_id"] = ObjectId(session_data["organization_id"])

            session_data.update({
                "created_at": datetime.now(timezone.utc),
                "status": SessionStatus.STARTING.value
            })
            await self.sessions.insert_one(session_data)
            return True
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            return False

    async def find_session(self, session_id: str, organization_id: str = None) -> Optional[dict]:
        try:
            from bson import ObjectId
            query = {"session_id": session_id}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            return await self.sessions.find_one(query)
        except Exception as e:
            logger.error(f"Error finding session {session_id}: {e}")
            return None

    async def update_session(self, session_id: str, updates: dict, organization_id: str = None) -> bool:
        try:
            from bson import ObjectId
            updates["updated_at"] = datetime.now(timezone.utc)
            query = {"session_id": session_id}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            result = await self.sessions.update_one(query, {"$set": updates})
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating session {session_id}: {e}")
            return False

    async def list_active_sessions(self, organization_id: str = None) -> List[dict]:
        try:
            from bson import ObjectId
            query = {"status": {"$in": [SessionStatus.STARTING.value, SessionStatus.RUNNING.value]}}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            cursor = self.sessions.find(query)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error listing active sessions: {e}")
            return []

    async def cleanup_old_sessions(self, hours: int = 24) -> int:
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
            result = await self.sessions.delete_many({
                "status": {"$in": [SessionStatus.COMPLETED.value, SessionStatus.FAILED.value]},
                "created_at": {"$lt": datetime.fromtimestamp(cutoff)}
            })
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error cleaning up sessions: {e}")
            return 0


_session_db_instance: Optional[AsyncSessionRecord] = None


def get_async_session_db() -> AsyncSessionRecord:
    global _session_db_instance
    if _session_db_instance is None:
        _session_db_instance = AsyncSessionRecord(get_mongo_client())
    return _session_db_instance
