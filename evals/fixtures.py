from typing import Optional
from bson import ObjectId

from evals.db import get_patient_db, ORG_ID_STR
from backend.database import get_mongo_client, MONGO_DB_NAME
from backend.sessions import AsyncSessionRecord
from backend.utils import parse_natural_date


class TestDB:

    def __init__(self):
        self.db = get_patient_db()
        self.session_db = AsyncSessionRecord(get_mongo_client())
        self.seeded_ids: list[str] = []
        self.session_ids: list[str] = []

    async def seed_patient(self, scenario: dict, workflow: str) -> str:
        patient_data = scenario.get("patient", {})

        patient_id = str(ObjectId())

        record = {
            "_id": ObjectId(patient_id),
            "patient_id": patient_id,
            "organization_id": ObjectId(ORG_ID_STR),
            "workflow": workflow,
            "call_status": "Not Started",
            **patient_data,
        }

        if "phone_number" in record:
            record["phone_number"] = ''.join(c for c in str(record["phone_number"]) if c.isdigit())

        # Normalize date_of_birth to ISO format (YYYY-MM-DD) to match parse_natural_date output
        if "date_of_birth" in record:
            normalized_dob = parse_natural_date(record["date_of_birth"])
            if normalized_dob:
                record["date_of_birth"] = normalized_dob

        if "patient_name" in record and "first_name" not in record:
            parts = record["patient_name"].split()
            if len(parts) >= 2:
                record["first_name"] = parts[0]
                record["last_name"] = " ".join(parts[1:])
            elif len(parts) == 1:
                record["first_name"] = parts[0]
                record["last_name"] = ""

        coll = get_mongo_client()[MONGO_DB_NAME].patients
        if record.get("phone_number"):
            await coll.delete_many({
                "phone_number": record["phone_number"],
                "organization_id": ObjectId(ORG_ID_STR),
            })

        await self.db.add_patient(record)
        self.seeded_ids.append(patient_id)

        return patient_id

    async def get_patient_state(self, patient_id: str, workflow: str = None) -> dict:
        patient = await self.db.find_patient_by_id(patient_id, ORG_ID_STR)
        if not patient:
            return {}

        # Common fields
        state = {
            "call_status": patient.get("call_status", ""),
        }

        if workflow == "eligibility_verification":
            state.update({
                "network_status": patient.get("network_status"),
                "plan_type": patient.get("plan_type"),
                "cpt_covered": patient.get("cpt_covered"),
                "copay_amount": patient.get("copay_amount"),
                "coinsurance_percent": patient.get("coinsurance_percent"),
                "prior_auth_required": patient.get("prior_auth_required"),
                "deductible_family": patient.get("deductible_family"),
                "deductible_family_met": patient.get("deductible_family_met"),
                "oop_max_family": patient.get("oop_max_family"),
                "oop_max_family_met": patient.get("oop_max_family_met"),
                "reference_number": patient.get("reference_number"),
            })
        else:  # lab_results or default
            state.update({
                "identity_verified": patient.get("identity_verified", False),
                "results_communicated": patient.get("results_communicated", False),
                "callback_confirmed": patient.get("callback_confirmed", False),
                "caller_phone_number": patient.get("caller_phone_number", ""),
            })

        return state

    async def get_full_patient(self, patient_id: str) -> Optional[dict]:
        return await self.db.find_patient_by_id(patient_id, ORG_ID_STR)

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Get session record by ID."""
        coll = get_mongo_client()[MONGO_DB_NAME].sessions
        return await coll.find_one({"session_id": session_id})

    async def get_captured_fields(self, patient_id: str) -> dict:
        """Get all eligibility fields written to DB."""
        patient = await self.db.find_patient_by_id(patient_id, ORG_ID_STR)
        if not patient:
            return {}

        fields = [
            "network_status", "plan_type", "plan_effective_date", "plan_term_date",
            "cpt_covered", "copay_amount", "coinsurance_percent", "deductible_applies",
            "prior_auth_required", "telehealth_covered",
            "deductible_individual", "deductible_individual_met",
            "deductible_family", "deductible_family_met",
            "oop_max_individual", "oop_max_individual_met",
            "oop_max_family", "oop_max_family_met",
            "reference_number", "call_status",
        ]
        return {f: patient.get(f) for f in fields if patient.get(f) is not None}

    async def create_session(self, session_id: str, workflow: str) -> bool:
        success = await self.session_db.create_session({
            "session_id": session_id,
            "organization_id": ORG_ID_STR,
            "client_name": f"demo_clinic_alpha/{workflow}",
            "call_type": "eval",
        })
        if success:
            self.session_ids.append(session_id)
        return success

    async def cleanup(self):
        for patient_id in self.seeded_ids:
            try:
                await self.db.delete_patient(patient_id, ORG_ID_STR)
            except Exception:
                pass
        self.seeded_ids.clear()

        for session_id in self.session_ids:
            try:
                coll = get_mongo_client()[MONGO_DB_NAME].sessions
                await coll.delete_one({"session_id": session_id})
            except Exception:
                pass
        self.session_ids.clear()
