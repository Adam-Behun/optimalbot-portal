#!/usr/bin/env python3
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(Path(__file__).parent / ".env.test")

from pipeline.runner import ConversationPipeline


TEST_PATIENT = {
    "patient_id": "test_001",
    "patient_name": "John Smith",
    "date_of_birth": "1985-03-15",
    "insurance_member_id": "ASD489RTYBBB",
    "insurance_company": "Blue Cross Insurance",
    "insurance_phone": "+15165853321",
    "cpt_code": "99213",
    "provider_npi": "1234567890",
    "provider_name": "Dr. Sarah Johnson",
    "facility": "Adam's Medical Practice",
    "appointment_time": "tomorrow at 2 PM"
}


async def run_test():
    room_url = os.getenv("TEST_DAILY_ROOM_URL")
    room_token = os.getenv("TEST_DAILY_ROOM_TOKEN")
    test_phone = os.getenv("TWILIO_PHONE_NUMBER", "+15165853321")

    if not room_url or not room_token:
        print("FAIL: TEST_DAILY_ROOM_URL and TEST_DAILY_ROOM_TOKEN required in .env.test")
        return

    pipeline = ConversationPipeline(
        client_name="prior_auth",
        session_id="integration_test",
        patient_id="test_001",
        patient_data=TEST_PATIENT,
        phone_number=test_phone,
        debug_mode=False
    )

    try:
        await pipeline.run(room_url, room_token, "integration_test")

        dtmf_actions = [t for t in pipeline.transcripts if t.get("type") == "ivr_action"]
        ivr_summary = [t for t in pipeline.transcripts if t.get("type") == "ivr_summary"]

        expected_path = ["2", "1"]
        actual_path = [t["content"].replace("Pressed ", "") for t in dtmf_actions]

        if actual_path == expected_path and any("Completed" in t.get("content", "") for t in ivr_summary):
            print("PASS")
        else:
            print(f"FAIL: Expected {expected_path}, got {actual_path}")

    except Exception as e:
        print(f"FAIL: {e}")


if __name__ == "__main__":
    asyncio.run(run_test())
