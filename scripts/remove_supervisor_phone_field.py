#!/usr/bin/env python3
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def main():
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    client = AsyncIOMotorClient(mongo_uri)
    db = client[os.getenv("MONGO_DB_NAME", "alfons")]

    print("=" * 60)
    print("Removing supervisor_phone from prior_auth schema")
    print("=" * 60)

    org = await db.organizations.find_one({"slug": "demo_clinic_alpha"})
    if not org:
        print("ERROR: demo_clinic_alpha organization not found")
        return

    prior_auth = org.get("workflows", {}).get("prior_auth", {})
    fields = prior_auth.get("patient_schema", {}).get("fields", [])
    new_fields = [f for f in fields if f.get("key") != "supervisor_phone"]

    if len(new_fields) == len(fields):
        print("supervisor_phone field not found - nothing to remove")
    else:
        result = await db.organizations.update_one(
            {"slug": "demo_clinic_alpha"},
            {"$set": {"workflows.prior_auth.patient_schema.fields": new_fields}}
        )
        print(f"Removed supervisor_phone field (modified: {result.modified_count})")

    result = await db.patients.update_many(
        {"workflow": "prior_auth"},
        {"$unset": {"supervisor_phone": ""}}
    )
    print(f"Removed supervisor_phone from {result.modified_count} patient records")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
