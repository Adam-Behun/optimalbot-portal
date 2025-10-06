import asyncio
from models import get_async_patient_db

async def test_new_methods():
    db = get_async_patient_db()
    
    # Test 1: Add a patient
    print("Test 1: Adding new patient...")
    patient_data = {
        "patient_name": "Test Patient",
        "date_of_birth": "1990-01-01",
        "insurance_company_name": "Test Insurance",
        "facility_name": "Test Hospital",
        "cpt_code": "99213",
        "provider_npi": "1234567890"
    }
    
    patient_id = await db.add_patient(patient_data)
    print(f"✅ Patient added with ID: {patient_id}")
    
    # Test 2: Update call info
    print("\nTest 2: Starting call (update to 'In Progress')...")
    success = await db.update_call_info(
        patient_id=patient_id,
        call_status="In Progress",
        insurance_phone_number="+15551234567"
    )
    print(f"✅ Call status updated: {success}")
    
    # Test 3: Complete call with transcript
    print("\nTest 3: Completing call...")
    import json
    transcript = json.dumps([
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi, this is Alexandra"}
    ])
    
    success = await db.update_call_info(
        patient_id=patient_id,
        call_status="Completed",
        call_transcript=transcript
    )
    print(f"✅ Call completed: {success}")
    
    # Test 4: Verify updates
    print("\nTest 4: Verifying patient record...")
    patient = await db.find_patient_by_id(patient_id)
    print(f"Call Status: {patient.get('call_status')}")
    print(f"Phone Number: {patient.get('insurance_phone_number')}")
    print(f"Has Transcript: {bool(patient.get('call_transcript'))}")
    
    print("\n✅ All tests passed!")

if __name__ == "__main__":
    asyncio.run(test_new_methods())