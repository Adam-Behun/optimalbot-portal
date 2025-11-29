import logging
from datetime import datetime
from typing import Optional, List
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

from backend.database import get_mongo_client, MONGO_DB_NAME

logger = logging.getLogger(__name__)


class AsyncOrganizationRecord:
    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[MONGO_DB_NAME]
        self.organizations = self.db.organizations

    async def _ensure_indexes(self):
        try:
            await self.organizations.create_index("slug", unique=True)
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    async def create(self, org_data: dict) -> Optional[str]:
        try:
            await self._ensure_indexes()
            now = datetime.utcnow().isoformat()
            org_data.update({"created_at": now, "updated_at": now})
            result = await self.organizations.insert_one(org_data)
            logger.info(f"Created organization: {org_data.get('name')} (ID: {result.inserted_id})")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating organization: {e}")
            return None

    async def get_by_id(self, org_id: str) -> Optional[dict]:
        try:
            return await self.organizations.find_one({"_id": ObjectId(org_id)})
        except Exception as e:
            logger.error(f"Error finding organization {org_id}: {e}")
            return None

    async def get_by_slug(self, slug: str) -> Optional[dict]:
        try:
            return await self.organizations.find_one({"slug": slug})
        except Exception as e:
            logger.error(f"Error finding organization by slug {slug}: {e}")
            return None

    async def update(self, org_id: str, update_fields: dict) -> bool:
        try:
            update_fields["updated_at"] = datetime.utcnow().isoformat()
            result = await self.organizations.update_one({"_id": ObjectId(org_id)}, {"$set": update_fields})
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating organization {org_id}: {e}")
            return False

    async def list_all(self) -> List[dict]:
        try:
            cursor = self.organizations.find().sort("created_at", -1)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error listing organizations: {e}")
            return []

    async def delete(self, org_id: str) -> bool:
        try:
            result = await self.organizations.delete_one({"_id": ObjectId(org_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting organization {org_id}: {e}")
            return False


_org_db_instance: Optional[AsyncOrganizationRecord] = None


def get_async_organization_db() -> AsyncOrganizationRecord:
    global _org_db_instance
    if _org_db_instance is None:
        _org_db_instance = AsyncOrganizationRecord(get_mongo_client())
    return _org_db_instance
