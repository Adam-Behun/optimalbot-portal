from datetime import datetime, timezone
from typing import Optional, List, Any, TYPE_CHECKING
from bson import ObjectId
from loguru import logger

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClient

from backend.database import get_mongo_client, MONGO_DB_NAME


class AsyncPatientRecord:
    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[MONGO_DB_NAME]
        self.patients = self.db.patients

    async def _ensure_indexes(self):
        try:
            await self.patients.create_index([("organization_id", 1), ("created_at", -1)])
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    async def find_patient_by_id(self, patient_id: str, organization_id: str = None) -> Optional[dict]:
        try:
            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            return await self.patients.find_one(query)
        except Exception as e:
            logger.error(f"Error finding patient {patient_id}: {e}")
            return None

    async def find_patients_by_organization(self, organization_id: str, workflow: str = None) -> List[dict]:
        try:
            query = {"organization_id": ObjectId(organization_id)}
            if workflow:
                query["workflow"] = workflow
            cursor = self.patients.find(query).sort("created_at", -1)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding patients for org {organization_id}: {e}")
            return []

    async def find_patients_by_status(self, status: str, organization_id: str = None) -> List[dict]:
        try:
            query = {"prior_auth_status": status}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            cursor = self.patients.find(query)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding patients with status {status}: {e}")
            return []

    async def add_patient(self, patient_data: dict) -> Optional[str]:
        try:
            await self._ensure_indexes()
            now = datetime.now(timezone.utc).isoformat()

            if "organization_id" in patient_data and isinstance(patient_data["organization_id"], str):
                patient_data["organization_id"] = ObjectId(patient_data["organization_id"])

            patient_data.update({
                "created_at": now,
                "updated_at": now,
                "call_status": patient_data.get("call_status", "Not Started")
            })

            result = await self.patients.insert_one(patient_data)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error adding patient: {e}")
            return None

    async def update_patient(self, patient_id: str, update_fields: dict, organization_id: str = None) -> bool:
        try:
            update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            result = await self.patients.update_one(query, {"$set": update_fields})
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating patient {patient_id}: {e}")
            return False

    async def update_field(self, patient_id: str, field_key: str, value: Any, organization_id: str = None) -> bool:
        return await self.update_patient(patient_id, {field_key: value}, organization_id)

    async def update_fields(self, patient_id: str, fields: dict, organization_id: str = None) -> bool:
        return await self.update_patient(patient_id, fields, organization_id)

    async def update_call_status(self, patient_id: str, status: str, organization_id: str = None) -> bool:
        return await self.update_patient(patient_id, {"call_status": status}, organization_id)

    async def save_call_transcript(
        self,
        patient_id: str,
        session_id: str,
        transcript_data: dict,
        organization_id: str = None
    ) -> bool:
        update_fields = {
            "last_call_session_id": session_id,
            "last_call_timestamp": datetime.now(timezone.utc).isoformat(),
            "call_transcript": transcript_data
        }
        return await self.update_patient(patient_id, update_fields, organization_id)

    async def get_call_transcript(self, patient_id: str) -> Optional[dict]:
        try:
            return await self.patients.find_one(
                {"_id": ObjectId(patient_id)},
                {"call_transcript": 1, "last_call_timestamp": 1, "last_call_session_id": 1}
            )
        except Exception as e:
            logger.error(f"Error getting transcript for {patient_id}: {e}")
            return None

    async def delete_patient(self, patient_id: str, organization_id: str = None) -> bool:
        try:
            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            result = await self.patients.delete_one(query)
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting patient {patient_id}: {e}")
            return False


_patient_db_instance: Optional[AsyncPatientRecord] = None


def get_async_patient_db() -> AsyncPatientRecord:
    global _patient_db_instance
    if _patient_db_instance is None:
        _patient_db_instance = AsyncPatientRecord(get_mongo_client())
    return _patient_db_instance
