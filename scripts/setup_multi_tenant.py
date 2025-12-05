#!/usr/bin/env python3
"""
Setup script for multi-tenant database.
Deletes existing collections and creates organizations + users.

Run: python scripts/setup_multi_tenant.py
"""

import asyncio
import os
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def main():
    # Connect to MongoDB
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    client = AsyncIOMotorClient(mongo_uri)
    db = client[os.getenv("MONGO_DB_NAME", "alfons")]

    print("=" * 60)
    print("Multi-Tenant Database Setup")
    print("=" * 60)

    # Step 1: Drop existing collections
    print("\n[1/5] Dropping existing collections...")
    collections_to_drop = ["audit_logs", "patients", "sessions", "users", "organizations"]
    for coll in collections_to_drop:
        try:
            await db.drop_collection(coll)
            print(f"  ✓ Dropped {coll}")
        except Exception as e:
            print(f"  ⚠ {coll}: {e}")

    # Step 2: Create DemoClinicAlpha organization
    print("\n[2/5] Creating DemoClinicAlpha organization...")
    alpha_org = {
        "name": "Demo Clinic Alpha",
        "slug": "demo_clinic_alpha",
        "branding": {
            "company_name": "Demo Clinic Alpha"
        },
        "workflows": {
            "prior_auth": {
                "enabled": True,
                "display_name": "Prior Authorization",
                "description": "Outbound calls to insurance companies for prior auth verification",
                "call_direction": "dial-out",
                "dial_out_phone_field": "insurance_phone",
                "patient_schema": {
                    "fields": [
                        {"key": "patient_name", "label": "Patient Name", "type": "string", "required": True, "display_in_list": True, "display_order": 1},
                        {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": True, "display_in_list": True, "display_order": 2},
                        {"key": "provider_call_back_phone", "label": "Provider Callback Phone", "type": "phone", "required": True, "display_in_list": True, "display_order": 3},
                        {"key": "insurance_member_id", "label": "Member ID", "type": "string", "required": True, "display_in_list": True, "display_order": 4},
                        {"key": "insurance_company_name", "label": "Insurance Company", "type": "string", "required": True, "display_in_list": False, "display_order": 5},
                        {"key": "insurance_phone", "label": "Insurance Phone", "type": "phone", "required": True, "display_in_list": True, "display_order": 6},
                        {"key": "provider_name", "label": "Provider Name", "type": "string", "required": True, "display_in_list": False, "display_order": 7},
                        {"key": "provider_npi", "label": "Provider NPI", "type": "string", "required": True, "display_in_list": False, "display_order": 8},
                        {"key": "facility_name", "label": "Facility", "type": "string", "required": True, "display_in_list": False, "display_order": 9},
                        {"key": "cpt_code", "label": "CPT Code", "type": "string", "required": True, "display_in_list": True, "display_order": 10},
                        {"key": "appointment_time", "label": "Appointment Time", "type": "datetime", "required": True, "display_in_list": False, "display_order": 11},
                        {"key": "prior_auth_status", "label": "Auth Status", "type": "select", "options": ["Pending", "Approved", "Denied"], "default": "Pending", "required": False, "display_in_list": True, "display_order": 13, "computed": True},
                        {"key": "reference_number", "label": "Reference #", "type": "string", "required": False, "display_in_list": True, "display_order": 14, "computed": True}
                    ]
                }
            },
            "patient_questions": {
                "enabled": True,
                "display_name": "Patient Questions",
                "description": "Inbound calls from patients with questions and inquiries",
                "call_direction": "dial-in",
                "patient_schema": {
                    "fields": [
                        {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},
                        {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 2, "computed": True},
                        {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 3, "computed": True},
                        {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 4, "computed": True},
                        {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 5, "computed": True},
                        {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 6, "computed": True},
                        {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 7, "computed": True},
                        {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 8, "computed": True}
                    ]
                }
            }
        },
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }

    result = await db.organizations.insert_one(alpha_org)
    alpha_org_id = result.inserted_id
    print(f"  ✓ Created DemoClinicAlpha (ID: {alpha_org_id})")

    # Create unique index on slug
    await db.organizations.create_index("slug", unique=True)

    # Step 3: Create DemoClinicBeta organization
    print("\n[3/5] Creating DemoClinicBeta organization...")
    beta_org = {
        "name": "Demo Clinic Beta",
        "slug": "demo_clinic_beta",
        "branding": {
            "company_name": "Demo Clinic Beta"
        },
        "workflows": {
            "patient_questions": {
                "enabled": True,
                "display_name": "Patient Questions",
                "description": "Inbound calls from patients with questions and inquiries",
                "call_direction": "dial-in",
                "patient_schema": {
                    "fields": [
                        {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},
                        {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 2, "computed": True},
                        {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 3, "computed": True},
                        {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 4, "computed": True},
                        {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 5, "computed": True},
                        {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 6, "computed": True},
                        {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 7, "computed": True},
                        {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 8, "computed": True}
                    ]
                }
            },
            "patient_intake": {
                "enabled": True,
                "display_name": "Patient Intake",
                "description": "Inbound calls for dental appointment scheduling - new and returning patients",
                "call_direction": "dial-in",
                "patient_schema": {
                    "fields": [
                        {"key": "appointment_type", "label": "Appointment Type", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},
                        {"key": "appointment_date", "label": "Appointment Date", "type": "string", "required": False, "display_in_list": True, "display_order": 2, "computed": True},
                        {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 3, "computed": True},
                        {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 4, "computed": True},
                        {"key": "phone_number", "label": "Phone Number", "type": "phone", "required": False, "display_in_list": True, "display_order": 5, "computed": True},
                        {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 6, "computed": True},
                        {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 7, "computed": True},
                        {"key": "email", "label": "Email", "type": "string", "required": False, "display_in_list": False, "display_order": 8, "computed": True},
                        {"key": "appointment_time", "label": "Appointment Time", "type": "string", "required": False, "display_in_list": False, "display_order": 9, "computed": True},
                        {"key": "appointment_reason", "label": "Appointment Reason", "type": "string", "required": False, "display_in_list": False, "display_order": 10, "computed": True},
                        {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 11, "computed": True},
                        {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 12, "computed": True},
                        {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 13, "computed": True},
                        {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 14, "computed": True}
                    ]
                }
            }
        },
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }

    result = await db.organizations.insert_one(beta_org)
    beta_org_id = result.inserted_id
    print(f"  ✓ Created DemoClinicBeta (ID: {beta_org_id})")

    # Step 4: Create admin user for DemoClinicAlpha
    print("\n[4/5] Creating admin user for DemoClinicAlpha...")
    import bcrypt
    from datetime import timedelta

    alpha_password = "REDACTED"
    alpha_hash = bcrypt.hashpw(alpha_password.encode(), bcrypt.gensalt()).decode()
    now = datetime.utcnow()

    alpha_user = {
        "email": "adambehun22@gmail.com",
        "hashed_password": alpha_hash,
        "password_history": [alpha_hash],
        "organization_id": alpha_org_id,
        "role": "admin",
        "status": "active",
        "failed_login_attempts": 0,
        "locked_at": None,
        "locked_reason": None,
        "last_login_at": None,
        "last_password_change": now.isoformat(),
        "password_expires_at": (now + timedelta(days=90)).isoformat(),
        "created_at": now.isoformat(),
        "created_by": None,
        "updated_at": now.isoformat()
    }

    result = await db.users.insert_one(alpha_user)
    print(f"  ✓ Created user: adambehun22@gmail.com (ID: {result.inserted_id})")

    # Create indexes for users
    await db.users.create_index("email", unique=True)
    await db.users.create_index("organization_id")

    # Step 5: Create admin user for DemoClinicBeta
    print("\n[5/5] Creating admin user for DemoClinicBeta...")

    beta_password = "REDACTED"
    beta_hash = bcrypt.hashpw(beta_password.encode(), bcrypt.gensalt()).decode()

    beta_user = {
        "email": "adam@datasova.com",
        "hashed_password": beta_hash,
        "password_history": [beta_hash],
        "organization_id": beta_org_id,
        "role": "admin",
        "status": "active",
        "failed_login_attempts": 0,
        "locked_at": None,
        "locked_reason": None,
        "last_login_at": None,
        "last_password_change": now.isoformat(),
        "password_expires_at": (now + timedelta(days=90)).isoformat(),
        "created_at": now.isoformat(),
        "created_by": None,
        "updated_at": now.isoformat()
    }

    result = await db.users.insert_one(beta_user)
    print(f"  ✓ Created user: adam@datasova.com (ID: {result.inserted_id})")

    # Summary
    print("\n" + "=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    print("\nOrganizations:")
    print(f"  • DemoClinicAlpha: {alpha_org_id}")
    print(f"    - Slug: demo_clinic_alpha")
    print(f"    - Workflows: prior_auth, patient_questions")
    print(f"    - User: adambehun22@gmail.com / REDACTED")
    print(f"\n  • DemoClinicBeta: {beta_org_id}")
    print(f"    - Slug: demo_clinic_beta")
    print(f"    - Workflows: patient_questions, patient_intake")
    print(f"    - User: adam@datasova.com / REDACTED")
    print("\nCollections created:")
    print("  • organizations")
    print("  • users")
    print("\nData Model:")
    print("  • Organizations have workflows, each with its own patient_schema")
    print("  • Patients stored flat with organization_id + workflow fields")
    print("  • Sessions store transcripts linked to patients")
    print("\nTest the setup:")
    print("  1. Login as Alpha user → select prior_auth or patient_questions workflow")
    print("  2. Login as Beta user → select patient_questions or patient_intake workflow")
    print("  3. Each workflow has its own patient schema and fields")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
