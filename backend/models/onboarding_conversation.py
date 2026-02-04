from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from bson import ObjectId
from loguru import logger

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClient

from backend.database import MONGO_DB_NAME, get_mongo_client

# Module-level flag to ensure indexes are only created once
_indexes_ensured = False


class AsyncOnboardingConversationRecord:
    def __init__(self, db_client: "AsyncIOMotorClient"):
        self.client = db_client
        self.db = db_client[MONGO_DB_NAME]
        self.conversations = self.db.onboarding_conversations

    async def _ensure_indexes(self):
        global _indexes_ensured
        if _indexes_ensured:
            return
        try:
            await self.conversations.create_index([
                ("organization_id", 1),
                ("workflow", 1),
                ("created_at", -1)
            ])
            _indexes_ensured = True
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    async def create_conversation(self, data: dict) -> Optional[str]:
        """Create a new onboarding conversation document.

        Note: organization_id is stored as a string slug (e.g., 'demo_clinic_alpha'),
        not as a MongoDB ObjectId. This is intentional for the onboarding workflow
        where we key conversations by org slug + workflow name.
        """
        try:
            await self._ensure_indexes()
            now = datetime.now(timezone.utc).isoformat()

            data.update({
                "status": data.get("status", "cleaned"),
                "approved_by": None,
                "approved_at": None,
                "created_at": now,
                "updated_at": now,
            })

            result = await self.conversations.insert_one(data)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating onboarding conversation: {e}")
            return None

    async def find_by_org_workflow(
        self, organization_id: str, workflow: str
    ) -> list[dict]:
        """Find all conversations for an organization and workflow."""
        try:
            query = {
                "organization_id": organization_id,
                "workflow": workflow,
            }
            cursor = self.conversations.find(query).sort("created_at", -1)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(
                f"Error finding conversations for {organization_id}/{workflow}: {e}"
            )
            return []

    async def find_by_id(self, conversation_id: str) -> Optional[dict]:
        """Find a single conversation by ID."""
        try:
            return await self.conversations.find_one({"_id": ObjectId(conversation_id)})
        except Exception as e:
            logger.error(f"Error finding conversation {conversation_id}: {e}")
            return None

    async def update_conversation(
        self, conversation_id: str, updates: dict
    ) -> bool:
        """Update a conversation document."""
        try:
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            result = await self.conversations.update_one(
                {"_id": ObjectId(conversation_id)},
                {"$set": updates}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating conversation {conversation_id}: {e}")
            return False

    async def approve_conversation(
        self, conversation_id: str, user_id: str
    ) -> bool:
        """Mark a conversation as approved."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = await self.conversations.update_one(
                {"_id": ObjectId(conversation_id)},
                {"$set": {
                    "status": "approved",
                    "approved_by": user_id,
                    "approved_at": now,
                    "updated_at": now,
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error approving conversation {conversation_id}: {e}")
            return False

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation document."""
        try:
            result = await self.conversations.delete_one(
                {"_id": ObjectId(conversation_id)}
            )
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting conversation {conversation_id}: {e}")
            return False


_onboarding_conversation_db_instance: Optional[AsyncOnboardingConversationRecord] = None


def get_async_onboarding_conversation_db() -> AsyncOnboardingConversationRecord:
    global _onboarding_conversation_db_instance
    if _onboarding_conversation_db_instance is None:
        _onboarding_conversation_db_instance = AsyncOnboardingConversationRecord(
            get_mongo_client()
        )
    return _onboarding_conversation_db_instance
