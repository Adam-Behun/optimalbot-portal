# test_daily_phone.py
import os
from dotenv import load_dotenv
import requests

load_dotenv()

api_key = os.getenv("DAILY_API_KEY")
headers = {"Authorization": f"Bearer {api_key}"}

print("Testing Daily.co Phone Number Setup\n" + "="*50)

# Test 1: Check domain config
print("\n1. Checking domain configuration...")
response = requests.get("https://api.daily.co/v1/", headers=headers)
domain_data = response.json()
print(f"   Domain: {domain_data.get('domain_name')}")
print(f"   HIPAA enabled: {domain_data.get('config', {}).get('hipaa')}")

# Test 2: Try alternative phone number endpoints
print("\n2. Trying to fetch phone numbers...")

# Try endpoint 1: /v1/phone-numbers
response1 = requests.get("https://api.daily.co/v1/phone-numbers", headers=headers)
print(f"   /v1/phone-numbers: {response1.status_code}")
if response1.status_code == 200:
    print(f"   Response: {response1.json()}")

# Try endpoint 2: /v1/sip-endpoints (sometimes phone numbers are here)
response2 = requests.get("https://api.daily.co/v1/sip-endpoints", headers=headers)
print(f"   /v1/sip-endpoints: {response2.status_code}")
if response2.status_code == 200:
    print(f"   Response: {response2.json()}")

# Test 3: Check if dial-out is enabled on domain
print("\n3. Checking dial-out capabilities...")
max_sip_pstn = domain_data.get('config', {}).get('max_sip_pstn_sessions_per_room')
print(f"   Max SIP/PSTN sessions per room: {max_sip_pstn}")

# Test 4: Try to create a test room with dial-out
print("\n4. Testing room creation with dial-out enabled...")
test_room_payload = {
    "name": "test-dialout-capability",
    "properties": {
        "enable_dialout": True
    }
}

room_response = requests.post(
    "https://api.daily.co/v1/rooms",
    headers=headers,
    json=test_room_payload
)

print(f"   Room creation status: {room_response.status_code}")
if room_response.status_code in [200, 201]:
    print(f"   ✓ Dial-out enabled successfully")
    room_data = room_response.json()
    print(f"   Room URL: {room_data.get('url')}")
    
    # Clean up test room
    requests.delete(
        f"https://api.daily.co/v1/rooms/{room_data.get('name')}",
        headers=headers
    )
    print(f"   ✓ Test room cleaned up")
else:
    print(f"   ✗ Error: {room_response.json()}")

print("\n" + "="*50)
print("\nPhone Number Information (from your setup):")
print(f"   Number: +1 516-202-8655")
print(f"   ID: 94d0eef5-d134-4ce6-86ac-98554378d886")