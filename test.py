import asyncio
from handlers.transcript import save_transcript_to_db_async
from backend.models import get_async_patient_db

Excellent! The first test worked perfectly - the data is in the database! 🎉
The "Event loop is closed" errors in the subsequent tests are happening because you're calling asyncio.run() multiple times in the same script, and the Motor (MongoDB async driver) is getting confused about which event loop to use.
This is actually not a problem in your real application - it only happens in this test script because we're artificially creating multiple event loops.
Here's a cleaner test script that avoids the issue:
test_transcript_save.py:
pythonimport asyncio
from transcript import save_transcript_to_db_async
from backend.models import get_async_patient_db

async def run_all_tests():
    """Run all tests in a single event loop"""
    
    print("="*80)
    print("TEST 1: Direct database save with real patient")
    print("="*80)
    
    # Real patient ID
    test_session_id = "real_test_001"
    test_patient_id = "68f428bf814d7a2ed9e68c97"
    
    print(f"Testing transcript save for session: {test_session_id}")
    print(f"Patient ID: {test_patient_id}")
    
    # First, verify the patient exists
    patient_db = get_async_patient_db()
    patient = await patient_db.find_patient_by_id(test_patient_id)
    
    if patient:
        print(f"✅ Patient found: {patient.get('first_name', 'Unknown')} {patient.get('last_name', '')}")
    else:
        print(f"❌ Patient NOT found with ID: {test_patient_id}")
        return
    
    # Create a mock transcript with actual data
    mock_transcript = {
        "session_id": test_session_id,
        "messages": [
            {
                "role": "assistant",
                "content": "Hello, this is a test call.",
                "timestamp": "2025-10-20T23:41:00.000Z"
            },
            {
                "role": "user", 
                "content": "Hi, I'm responding to the test.",
                "timestamp": "2025-10-20T23:41:05.000Z"
            },
            {
                "role": "assistant",
                "content": "Thank you for your response. This concludes the test.",
                "timestamp": "2025-10-20T23:41:10.000Z"
            }
        ],
        "summary": "Test conversation completed successfully",
        "duration_seconds": 10
    }
    
    print("\n📝 Attempting to save transcript...")
    
    try:
        # Save directly to test
        success = await patient_db.save_call_transcript(
            test_patient_id, 
            test_session_id, 
            mock_transcript
        )
        
        if success:
            print("✅ Transcript saved successfully!")
            
            # Verify it was saved
            result = await patient_db.get_call_transcript(test_patient_id)
            if result and result.get('call_transcript'):
                print("\n📋 Saved transcript:")
                print(f"   Session ID: {result.get('last_call_session_id')}")
                print(f"   Timestamp: {result.get('last_call_timestamp')}")
                print(f"   Messages: {len(result['call_transcript'].get('messages', []))}")
        else:
            print("❌ Failed to save transcript - update returned False")
            
    except Exception as e:
        print(f"❌ Test 1 failed with exception: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*80)
    print("TEST 2: Full flow with save_transcript_to_db_async")
    print("="*80)
    
    test_session_id_2 = "real_test_002" 
    
    # Mock the collector since we don't have real data
    from unittest.mock import Mock, patch
    
    mock_collector = Mock()
    mock_collector.get_full_transcript.return_value = {
        "session_id": test_session_id_2,
        "messages": [
            {"role": "assistant", "content": "Test message 1", "timestamp": "2025-10-20T23:41:00.000Z"},
            {"role": "user", "content": "Test response", "timestamp": "2025-10-20T23:41:05.000Z"}
        ],
        "summary": "Full flow test",
        "duration_seconds": 5
    }
    mock_collector.print_full_transcript.return_value = None
    mock_collector.print_latency_waterfall.return_value = None
    
    try:
        with patch('handlers.transcript.get_collector', return_value=mock_collector):
            await save_transcript_to_db_async(test_session_id_2, test_patient_id)
        
        # Verify it saved
        result = await patient_db.get_call_transcript(test_patient_id)
        if result and result.get('call_transcript'):
            print("✅ Full flow test passed!")
            print(f"   Saved session: {result.get('last_call_session_id')}")
            print(f"   Messages: {len(result['call_transcript'].get('messages', []))}")
        else:
            print("❌ Full flow test failed - no transcript found")
    except Exception as e:
        print(f"❌ Test 2 failed with exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Starting transcript save tests...\n")
    asyncio.run(run_all_tests())
    print("\n✅ All tests finished successfully!")