#!/usr/bin/env python3
"""
Add patient_intake workflow to demo_clinic_alpha organization.

Run: python scripts/add_patient_intake_workflow_alpha.py
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
    print("Adding patient_intake workflow to demo_clinic_alpha")
    print("=" * 60)

    # Define the patient_intake workflow schema
    patient_intake_workflow = {
        "enabled": True,
        "display_name": "Patient Intake",
        "description": "Inbound calls for dental appointment scheduling - new and returning patients",
        "call_direction": "dial-in",
        "patient_schema": {
            "fields": [
                # Appointment type (New Patient or Returning Patient)
                {"key": "appointment_type", "label": "Appointment Type", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},

                # Appointment info
                {"key": "appointment_date", "label": "Appointment Date", "type": "date", "required": False, "display_in_list": True, "display_order": 2, "computed": True},

                # Patient info
                {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 3, "computed": True},
                {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 4, "computed": True},
                {"key": "phone_number", "label": "Phone Number", "type": "phone", "required": False, "display_in_list": True, "display_order": 5, "computed": True},

                # Detail view only fields
                {"key": "email", "label": "Email", "type": "string", "required": False, "display_in_list": False, "display_order": 6, "computed": True},
                {"key": "appointment_time", "label": "Appointment Time", "type": "time", "required": False, "display_in_list": False, "display_order": 7, "computed": True},
                {"key": "appointment_reason", "label": "Appointment Reason", "type": "string", "required": False, "display_in_list": False, "display_order": 8, "computed": True},

                # System fields
                {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 9, "computed": True},
                {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 10, "computed": True},
                {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 11, "computed": True},
                {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 12, "computed": True}
            ]
        }
    }

    # Update demo_clinic_alpha organization to add patient_intake workflow
    result = await db.organizations.update_one(
        {"slug": "demo_clinic_alpha"},
        {
            "$set": {
                "workflows.patient_intake": patient_intake_workflow,
                "updated_at": datetime.utcnow().isoformat()
            }
        }
    )

    if result.modified_count > 0:
        print("  ✓ Added patient_intake workflow to demo_clinic_alpha")
    else:
        print("  ⚠ No changes made (organization not found or workflow already exists)")

    # Verify the update
    org = await db.organizations.find_one({"slug": "demo_clinic_alpha"})
    if org:
        workflows = org.get("workflows", {})
        print(f"\n  demo_clinic_alpha now has {len(workflows)} workflow(s):")
        for wf_name, wf_config in workflows.items():
            enabled = "✓" if wf_config.get("enabled") else "✗"
            display_name = wf_config.get("display_name", wf_name)
            direction = wf_config.get("call_direction", "unknown")
            print(f"    [{enabled}] {wf_name}: {display_name} ({direction})")

            if wf_name == "patient_intake":
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
