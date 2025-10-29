"""MongoDB session tracking for active voice calls"""
import os
import logging
from datetime import datetime
from typing import Optional, List
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class AsyncSessionRecord:
    """Async database operations for call sessions - replaces in-memory active_pipelines dict"""

    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[os.getenv("MONGO_DB_NAME", "alfons")]
        self.sessions = self.db.sessions

    async def create_session(self, session_data: dict) -> bool:
        """Create new session record when call starts"""
        try:
            session_data.update({
                "created_at": datetime.utcnow(),
                "status": "starting"
            })
            await self.sessions.insert_one(session_data)
            return True
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            return False

    async def find_session(self, session_id: str) -> Optional[dict]:
        """Find session by session_id"""
        try:
            return await self.sessions.find_one({"session_id": session_id})
        except Exception as e:
            logger.error(f"Error finding session {session_id}: {e}")
            return None

    async def update_session(self, session_id: str, updates: dict) -> bool:
        """Update session fields"""
        try:
            updates["updated_at"] = datetime.utcnow()
            result = await self.sessions.update_one(
                {"session_id": session_id},
                {"$set": updates}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating session {session_id}: {e}")
            return False

    async def list_active_sessions(self) -> List[dict]:
        """Get all running sessions"""
        try:
            cursor = self.sessions.find({"status": {"$in": ["starting", "running"]}})
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error listing active sessions: {e}")
            return []

    async def cleanup_old_sessions(self, hours: int = 24) -> int:
        """Delete completed/failed sessions older than X hours"""
        try:
            cutoff = datetime.utcnow().timestamp() - (hours * 3600)
            result = await self.sessions.delete_many({
                "status": {"$in": ["completed", "failed"]},
                "created_at": {"$lt": datetime.fromtimestamp(cutoff)}
            })
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error cleaning up sessions: {e}")
            return 0


# Singleton instance
_session_db_instance = None

def get_async_session_db() -> AsyncSessionRecord:
    """Get singleton session database instance"""
    global _session_db_instance
    if _session_db_instance is None:
        client = AsyncIOMotorClient(
            os.getenv("MONGO_URI"),
            maxPoolSize=10,
            serverSelectionTimeoutMS=5000
        )
        _session_db_instance = AsyncSessionRecord(client)
    return _session_db_instance
