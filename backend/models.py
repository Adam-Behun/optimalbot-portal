from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, List
import os
from datetime import datetime
from dotenv import load_dotenv
import logging

# Load environment variables
logger = logging.getLogger(__name__)
load_dotenv()

class AsyncPatientRecord:
    """Asynchronous PatientRecord class with full schema support"""
    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db_name = os.getenv("MONGO_DB_NAME", "alfons")
        self.db = db_client[self.db_name]
        self.patients = self.db.patients
    
    async def find_patient_by_id(self, patient_id: str) -> Optional[dict]:
        """Find patient by MongoDB ObjectId and map to schema format"""
        from bson import ObjectId
        try:
            patient = await self.patients.find_one({"_id": ObjectId(patient_id)})
            if not patient:
                return None
            
            # ✅ Map database fields to schema fields
            mapped_patient = {
                "_id": patient["_id"],
                "name": patient.get("patient_name", ""),  # patient_name → name
                "patient_name": patient.get("patient_name", ""),  # Keep original too
                "date_of_birth": patient.get("date_of_birth", ""),
                "insurance_member_id": patient.get("insurance_member_id", ""),
                "insurance_company": patient.get("insurance_company_name", ""),  # insurance_company_name → insurance_company
                "insurance_company_name": patient.get("insurance_company_name", ""),  # Keep original
                "insurance_phone": patient.get("insurance_phone", ""),
                "facility": patient.get("facility_name", ""),  # facility_name → facility
                "facility_name": patient.get("facility_name", ""),  # Keep original
                "cpt_code": patient.get("cpt_code", ""),
                "provider_npi": patient.get("provider_npi", ""),
                "provider_name": patient.get("provider_name", ""),
                "appointment_time": patient.get("appointment_time", ""),
                "call_status": patient.get("call_status", ""),
                "prior_auth_status": patient.get("prior_auth_status", ""),
            }
            
            return mapped_patient
        except Exception as e:
            logger.error(f"Error finding patient: {e}")
            return None
    
    async def get_complete_patient_record(self, patient_id: str) -> Optional[dict]:
        """Get complete patient record with all information"""
        return await self.find_patient_by_id(patient_id)
    
    async def update_prior_auth_status(self, patient_id: str, status: str, reference_number: str = None) -> bool:
        """Update the prior authorization status and optionally the reference number for a patient"""
        from bson import ObjectId
        try:
            update_doc = {
                "prior_auth_status": status,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            if reference_number:
                update_doc["reference_number"] = reference_number
            
            result = await self.patients.update_one(
                {"_id": ObjectId(patient_id)},
                {"$set": update_doc}
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"Error updating patient: {e}")
            return False

    async def update_call_status(
        self, 
        patient_id: str, 
        status: str
    ) -> bool:
        """Update call status: 'Not Started' | 'In Progress' | 'Completed' | 'Failed' | 'Completed - Left VM'"""
        from bson import ObjectId
        try:
            result = await self.patients.update_one(
                {"_id": ObjectId(patient_id)},
                {"$set": {
                    "call_status": status,
                    "updated_at": datetime.utcnow().isoformat()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"Error updating call status: {e}")
            return False
    
    async def find_patients_pending_auth(self) -> List[dict]:
        """Find all patients with pending authorization"""
        try:
            cursor = self.patients.find({"prior_auth_status": "Pending"})
            return await cursor.to_list(length=None)
        except:
            return []
    
    async def add_patient(self, patient_data: dict) -> Optional[str]:
        """Insert a new patient record"""
        try:
            patient_data["created_at"] = datetime.utcnow().isoformat()
            patient_data["updated_at"] = datetime.utcnow().isoformat()
            
            if "call_status" not in patient_data:
                patient_data["call_status"] = "Not Started"
            if "prior_auth_status" not in patient_data:
                patient_data["prior_auth_status"] = "Pending"
            
            result = await self.patients.insert_one(patient_data)
            return str(result.inserted_id)
        except Exception as e:
            print(f"Error adding patient: {e}")
            return None
    
    async def delete_patient(self, patient_id: str) -> bool:
        """Delete a patient by ObjectId"""
        from bson import ObjectId
        try:
            result = await self.patients.delete_one({"_id": ObjectId(patient_id)})
            return result.deleted_count > 0
        except Exception as e:
            print(f"Error deleting patient: {e}")
            return False
    
    async def update_call_info(
        self, 
        patient_id: str, 
        call_status: str,
        insurance_phone_number: str = None,
        call_transcript: str = None
    ) -> bool:
        """Update call-related fields for a patient"""
        from bson import ObjectId
        try:
            update_doc = {
                "call_status": call_status,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            if insurance_phone_number:
                update_doc["insurance_phone_number"] = insurance_phone_number
            
            if call_transcript:
                update_doc["call_transcript"] = call_transcript
            
            result = await self.patients.update_one(
                {"_id": ObjectId(patient_id)},
                {"$set": update_doc}
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"Error updating call info: {e}")
            return False

    async def save_call_transcript(
        self, 
        patient_id: str, 
        session_id: str,
        transcript_data: dict
    ) -> bool:
        """
        Save complete call transcript with all turns and metrics.
        
        Args:
            patient_id: Patient MongoDB ObjectId
            session_id: Call session ID
            transcript_data: Full transcript from MonitoringCollector
        """
        from bson import ObjectId
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            update_doc = {
                "last_call_session_id": session_id,
                "last_call_timestamp": datetime.utcnow().isoformat(),
                "call_transcript": transcript_data,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            result = await self.patients.update_one(
                {"_id": ObjectId(patient_id)},
                {"$set": update_doc}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error saving transcript: {e}")
            return False

    async def get_call_transcript(self, patient_id: str) -> Optional[dict]:
        """Get the last call transcript for a patient."""
        from bson import ObjectId
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            patient = await self.patients.find_one(
                {"_id": ObjectId(patient_id)},
                {"call_transcript": 1, "last_call_timestamp": 1, "last_call_session_id": 1}
            )
            return patient if patient else None
        except Exception as e:
            logger.error(f"Error getting transcript: {e}")
            return None

def get_async_db_client():
    """Get asynchronous MongoDB client"""
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    print(f"DEBUG: MONGO_URI = {mongo_uri}")
    return AsyncIOMotorClient(mongo_uri)

# Global async client instance
_async_client = None

def get_async_patient_db() -> AsyncPatientRecord:
    """Get async patient database instance"""
    global _async_client
    if not _async_client:
        _async_client = get_async_db_client()
    return AsyncPatientRecord(_async_client)