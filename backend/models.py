import os
import logging
from datetime import datetime
from typing import Optional, List
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class AsyncPatientRecord:
    """Async database operations for patient records"""
    
    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[os.getenv("MONGO_DB_NAME", "alfons")]
        self.patients = self.db.patients
    
    async def find_patient_by_id(self, patient_id: str) -> Optional[dict]:
        """Find patient by MongoDB ObjectId"""
        try:
            patient = await self.patients.find_one({"_id": ObjectId(patient_id)})
            return patient
        except Exception as e:
            logger.error(f"Error finding patient {patient_id}: {e}")
            return None
    
    async def find_patients_by_status(self, status: str) -> List[dict]:
        """Find all patients with a specific prior auth status"""
        try:
            cursor = self.patients.find({"prior_auth_status": status})
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding patients with status {status}: {e}")
            return []
    
    async def add_patient(self, patient_data: dict) -> Optional[str]:
        """Insert a new patient record"""
        try:
            now = datetime.utcnow().isoformat()
            patient_data.update({
                "created_at": now,
                "updated_at": now,
                "call_status": patient_data.get("call_status", "Not Started"),
                "prior_auth_status": patient_data.get("prior_auth_status", "Pending")
            })
            
            result = await self.patients.insert_one(patient_data)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error adding patient: {e}")
            return None
    
    async def update_patient(self, patient_id: str, update_fields: dict) -> bool:
        """Generic update method for any patient fields"""
        try:
            update_fields["updated_at"] = datetime.utcnow().isoformat()
            
            result = await self.patients.update_one(
                {"_id": ObjectId(patient_id)},
                {"$set": update_fields}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating patient {patient_id}: {e}")
            return False
    
    async def update_prior_auth(
        self, 
        patient_id: str, 
        status: str, 
        reference_number: Optional[str] = None
    ) -> bool:
        """Update prior authorization status and reference number"""
        update_fields = {"prior_auth_status": status}
        if reference_number:
            update_fields["reference_number"] = reference_number
        
        return await self.update_patient(patient_id, update_fields)
    
    async def update_call_status(self, patient_id: str, status: str) -> bool:
        """Update call status"""
        return await self.update_patient(patient_id, {"call_status": status})
    
    async def save_call_transcript(
        self, 
        patient_id: str, 
        session_id: str,
        transcript_data: dict
    ) -> bool:
        """Save call transcript and metadata"""
        update_fields = {
            "last_call_session_id": session_id,
            "last_call_timestamp": datetime.utcnow().isoformat(),
            "call_transcript": transcript_data
        }
        return await self.update_patient(patient_id, update_fields)
    
    async def get_call_transcript(self, patient_id: str) -> Optional[dict]:
        """Get the last call transcript for a patient"""
        try:
            patient = await self.patients.find_one(
                {"_id": ObjectId(patient_id)},
                {"call_transcript": 1, "last_call_timestamp": 1, "last_call_session_id": 1}
            )
            return patient
        except Exception as e:
            logger.error(f"Error getting transcript for {patient_id}: {e}")
            return None
    
    async def delete_patient(self, patient_id: str) -> bool:
        """Delete a patient by ObjectId"""
        try:
            result = await self.patients.delete_one({"_id": ObjectId(patient_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting patient {patient_id}: {e}")
            return False


# Singleton pattern for database client
_async_client: Optional[AsyncIOMotorClient] = None

def get_async_patient_db() -> AsyncPatientRecord:
    """Get or create async patient database instance"""
    global _async_client
    if not _async_client:
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        logger.info(f"Connecting to MongoDB: {mongo_uri}")
        _async_client = AsyncIOMotorClient(mongo_uri)
    
    return AsyncPatientRecord(_async_client)