import os
from pathlib import Path

from bson import ObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
os.environ["MONGO_DB_NAME"] = "alfons_test"

from backend.database import get_mongo_client
from backend.models.patient import AsyncPatientRecord

ORG_OBJECT_ID = ObjectId("507f1f77bcf86cd799439011")
ORG_ID_STR = str(ORG_OBJECT_ID)

_patient_db = None

def get_patient_db() -> AsyncPatientRecord:
    global _patient_db
    if _patient_db is None:
        _patient_db = AsyncPatientRecord(get_mongo_client())
    return _patient_db
