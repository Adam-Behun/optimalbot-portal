#!/usr/bin/env python3
"""
Update demo_clinic_beta organization schema without recreating the database.

Run: python scripts/update_beta_schema.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

load_dotenv()


async def main():
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    client = AsyncIOMotorClient(mongo_uri)
    db = client[os.getenv("MONGO_DB_NAME", "alfons")]

    print("=" * 60)
    print("Updating demo_clinic_beta schema")
    print("=" * 60)

    new_fields = [
        {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 1, "computed": True},
        {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 2, "computed": True},
        {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 3, "computed": True},
        {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 4, "computed": True},
        {"key": "phone", "label": "Caller Phone", "type": "phone", "required": False, "display_in_list": False, "display_order": 5},
        {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": True, "display_order": 6, "computed": True},
        {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 7, "computed": True},
        {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": True, "display_order": 8, "computed": True}
    ]

    result = await db.organizations.update_one(
        {"slug": "demo_clinic_beta"},
        {
            "$set": {
                "workflows.patient_questions.patient_schema.fields": new_fields,
                "workflows.patient_questions.call_direction": "dial-in",
                "workflows.patient_questions.display_name": "Patient Questions",
                "workflows.patient_questions.description": "Inbound calls from patients with questions and inquiries",
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    if result.modified_count > 0:
        print("  ✓ Updated demo_clinic_beta patient_questions schema")
    else:
        print("  ⚠ No changes made (organization not found or schema unchanged)")

    org = await db.organizations.find_one({"slug": "demo_clinic_beta"})
    if org:
        fields = org.get("workflows", {}).get("patient_questions", {}).get("patient_schema", {}).get("fields", [])
        print(f"\n  Schema now has {len(fields)} fields:")
        for f in fields:
            print(f"    - {f['key']}: {f['label']}")

    client.close()
    print("\n" + "=" * 60)
    print("Done! Log out and log back in to see the changes.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
