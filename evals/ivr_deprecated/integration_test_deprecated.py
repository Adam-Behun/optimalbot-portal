import asyncio
import os
import sys
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
    "insurance_phone": "+15165853321",  # Twilio IVR number
    "cpt_code": "99213",
    "provider_npi": "1234567890",
    "provider_name": "Dr. Sarah Johnson",
    "facility": "Adam's Medical Practice",
    "appointment_time": "tomorrow at 2 PM"
}


async def run_test():
    room_url = os.getenv("TEST_DAILY_ROOM_URL")
    room_token = os.getenv("TEST_DAILY_ROOM_TOKEN")
    twilio_phone = os.getenv("TWILIO_PHONE_NUMBER", "+15165853321")
    your_phone = os.getenv("TEST_TRANSFER_NUMBER", "+15165667132")

    if not room_url or not room_token:
        print("❌ FAIL: TEST_DAILY_ROOM_URL and TEST_DAILY_ROOM_TOKEN required in .env.test")
        print("\nGet credentials from: https://dashboard.daily.co")
        return

    print("="*70)
    print("🧪 E2E INTEGRATION TEST: IVR Navigation → Human Conversation")
    print("="*70)
    print("\n📞 Test Configuration:")
    print(f"   Twilio IVR Number: {twilio_phone}")
    print(f"   Your Phone: {your_phone}")
    print(f"   Patient: {TEST_PATIENT['patient_name']}")
    print("\n📋 Test Instructions:")
    print("   1. Ensure twilio_ivr_server.py is running (port 5001)")
    print("   2. Ensure ngrok is exposing port 5001")
    print(f"   3. Your phone ({your_phone}) will ring after IVR navigation")
    print("   4. Answer and say: 'Hello, this is [your name] from Blue Cross'")
    print("   5. Bot will greet you and state purpose")
    print("   6. Continue conversation to test verification flow")
    print("\n⏳ Starting test in 5 seconds...\n")

    await asyncio.sleep(5)

    pipeline = ConversationPipeline(
        client_name="prior_auth",
        session_id="e2e_integration_test",
        patient_id="test_001",
        patient_data=TEST_PATIENT,
        phone_number=twilio_phone,  # Start with Twilio IVR
        debug_mode=True
    )

    try:
        print("🚀 Starting pipeline...")
        await pipeline.run(room_url, room_token, "e2e_integration_test")

        # Analyze results
        print("\n" + "="*70)
        print("📊 TEST RESULTS")
        print("="*70)

        # Check IVR navigation
        dtmf_actions = [t for t in pipeline.transcripts if t.get("type") == "ivr_action"]
        ivr_summary = [t for t in pipeline.transcripts if t.get("type") == "ivr_summary"]

        print("\n🔢 IVR Navigation:")
        expected_path = ["2", "1"]  # Provider Services → Eligibility
        actual_path = [t["content"].replace("Pressed ", "") for t in dtmf_actions]
        print(f"   Expected: {expected_path}")
        print(f"   Actual: {actual_path}")

        ivr_success = (
            actual_path == expected_path and
            any("Completed" in t.get("content", "") for t in ivr_summary)
        )
        print(f"   Status: {'✅ PASS' if ivr_success else '❌ FAIL'}")

        # Check conversation states
        print("\n💬 Conversation Flow:")
        user_messages = [t for t in pipeline.transcripts if t.get("role") == "user"]
        assistant_messages = [t for t in pipeline.transcripts if t.get("role") == "assistant"]

        print(f"   User messages: {len(user_messages)}")
        print(f"   Assistant messages: {len(assistant_messages)}")

        has_greeting = any("greeting" in str(pipeline.conversation_context.current_state).lower() for _ in [1])
        has_conversation = len(user_messages) > 0 and len(assistant_messages) > 0

        print(f"   Greeting state reached: {'✅ Yes' if has_greeting else '❌ No'}")
        print(f"   Conversation occurred: {'✅ Yes' if has_conversation else '❌ No'}")

        # Print transcript
        print("\n📝 Full Transcript:")
        print("-"*70)
        for entry in pipeline.transcripts:
            role = entry.get("role", "system")
            content = entry.get("content", "")
            entry_type = entry.get("type", "")

            if role == "user":
                print(f"USER: {content}")
            elif role == "assistant":
                print(f"ASSISTANT: {content}")
            elif role == "system" and entry_type == "ivr_action":
                print(f"[IVR ACTION] {content}")
            elif role == "system" and entry_type == "ivr_summary":
                print(f"[IVR] {content}")

        # Overall result
        print("\n" + "="*70)
        overall_pass = ivr_success and has_conversation
        if overall_pass:
            print("✅ OVERALL: PASS - Full E2E flow completed successfully!")
        else:
            print("❌ OVERALL: FAIL - Check results above for details")
        print("="*70)

    except Exception as e:
        print(f"\n❌ FAIL: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_test())
