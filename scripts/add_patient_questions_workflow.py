#!/usr/bin/env python3
"""
Add patient_questions workflow to demo_clinic_beta organization.

Run: python scripts/add_patient_questions_workflow.py
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
    print("Adding patient_questions workflow to demo_clinic_beta")
    print("=" * 60)

    # Define the patient_questions workflow schema
    patient_questions_workflow = {
        "enabled": True,
        "display_name": "Patient Questions",
        "description": "Inbound calls from patients with questions and inquiries",
        "call_direction": "dial-in",
        "patient_schema": {
            "fields": [
                # List view fields
                {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},
                {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 2, "computed": True},

                # Detail view only fields
                {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 3, "computed": True},
                {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 4, "computed": True},

                # System fields
                {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 5, "computed": True},
                {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 6, "computed": True},
                {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 7, "computed": True},
                {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 8, "computed": True}
            ]
        }
    }

    # Update demo_clinic_beta organization to add patient_questions workflow
    result = await db.organizations.update_one(
        {"slug": "demo_clinic_beta"},
        {
            "$set": {
                "workflows.patient_questions": patient_questions_workflow,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    if result.modified_count > 0:
        print("  ✓ Added patient_questions workflow to demo_clinic_beta")
    else:
        print("  ⚠ No changes made (organization not found or workflow already exists)")

    # Verify the update
    org = await db.organizations.find_one({"slug": "demo_clinic_beta"})
    if org:
        workflows = org.get("workflows", {})
        print(f"\n  demo_clinic_beta now has {len(workflows)} workflow(s):")
        for wf_name, wf_config in workflows.items():
            enabled = "✓" if wf_config.get("enabled") else "✗"
            display_name = wf_config.get("display_name", wf_name)
            direction = wf_config.get("call_direction", "unknown")
            print(f"    [{enabled}] {wf_name}: {display_name} ({direction})")

            if wf_name == "patient_questions":
                fields = wf_config.get("patient_schema", {}).get("fields", [])
                print(f"        Schema has {len(fields)} fields:")
                for f in fields:
                    print(f"          - {f['key']}: {f['label']}")

    client.close()
    print("\n" + "=" * 60)
    print("Done! Log out and log back in to see the new workflow.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
