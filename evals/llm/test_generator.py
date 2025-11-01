"""
Synthetic test data generator for creating eval test cases.

This module uses Claude to generate realistic test scenarios for each conversation state.
Follows the pattern from Anthropic's "Generate Synthetic Test Data" cookbook.
"""

import os
from typing import Dict, List, Any, Optional
from anthropic import Anthropic
import json


class TestDataGenerator:
    """
    Generates synthetic test cases for evaluation.

    Each test case includes:
    - Patient data (synthetic but realistic)
    - User utterance (what insurance rep says)
    - Expected behavior (what bot should do)
    - Test scenario description
    """

    def __init__(self, model: str = "claude-sonnet-4-5"):
        """Initialize generator with Claude API."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def generate_greeting_test_cases(self, num_cases: int = 5) -> List[Dict[str, Any]]:
        """
        Generate test cases for greeting state.

        Args:
            num_cases: Number of test cases to generate

        Returns:
            List of test case dicts
        """
        prompt = """You are generating test cases for a healthcare voice AI that greets insurance representatives.

CONTEXT:
- The AI just reached a human representative after navigating IVR
- The AI is Alexandra from Adam's Medical Practice
- Purpose: verify insurance benefits for a patient's upcoming procedure
- The greeting should be professional, concise, and state the purpose

TASK:
Generate realistic scenarios for different types of human responses after reaching the representative.

For each test case, provide:
1. scenario_id: Unique identifier (e.g., "greeting_friendly_rep", "greeting_confused_rep")
2. description: What this scenario tests
3. user_utterance: What the insurance representative says when they answer
4. expected_behavior: How the AI should respond (professional greeting, state purpose)

Generate scenarios including:
- Friendly, cooperative representatives
- Busy/rushed representatives
- Confused representatives who didn't understand the transfer
- Representatives asking for clarification
- Representatives who immediately ask for patient info
- Representatives from wrong department who need to transfer

Output format:
<test_cases>
[
  {
    "scenario_id": "...",
    "description": "...",
    "user_utterance": "...",
    "expected_behavior": "..."
  },
  ...
]
</test_cases>

Generate exactly """ + str(num_cases) + """ diverse, realistic test cases."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract JSON from response
        content = response.content[0].text
        test_cases_str = content[content.find("["):content.rfind("]")+1]
        test_cases = json.loads(test_cases_str)

        return test_cases

    def generate_verification_test_cases(self, num_cases: int = 5) -> List[Dict[str, Any]]:
        """
        Generate test cases for verification state.

        Args:
            num_cases: Number of test cases to generate

        Returns:
            List of test case dicts
        """
        prompt = """You are generating test cases for a healthcare voice AI that provides patient information to verify insurance coverage.

CONTEXT:
- The AI is in a conversation with an insurance representative
- The AI needs to provide patient information (name, DOB, member ID, CPT code, NPI, etc.)
- The representative will ask for specific information
- The AI must provide ONLY the information it has, never guess or fabricate
- The AI should handle various types of questions and requests

TASK:
Generate realistic scenarios for different types of information requests and challenges.

For each test case, provide:
1. scenario_id: Unique identifier (e.g., "verify_simple_info_request", "verify_missing_data")
2. description: What this scenario tests
3. user_utterance: What the insurance representative asks/says
4. expected_behavior: How the AI should respond

Generate scenarios including:
- Simple information requests (DOB, member ID)
- Multiple pieces of information requested at once
- Requests for information the AI doesn't have
- Requests to repeat or spell out information
- Representative asking clarifying questions
- Representative providing authorization status (approved/denied)
- Representative asking for reference number documentation

Output format:
<test_cases>
[
  {
    "scenario_id": "...",
    "description": "...",
    "user_utterance": "...",
    "expected_behavior": "..."
  },
  ...
]
</test_cases>

Generate exactly """ + str(num_cases) + """ diverse, realistic test cases."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract JSON from response
        content = response.content[0].text
        test_cases_str = content[content.find("["):content.rfind("]")+1]
        test_cases = json.loads(test_cases_str)

        return test_cases

    def generate_patient_data(self, num_patients: int = 1) -> List[Dict[str, Any]]:
        """
        Generate synthetic patient data for testing.

        Args:
            num_patients: Number of patient records to generate

        Returns:
            List of patient data dicts
        """
        prompt = """Generate realistic synthetic patient data for prior authorization testing.

Each patient record should include:
- patient_id: MongoDB ObjectId format (24 character hex string)
- patient_name: Realistic full name
- date_of_birth: Format "YYYY-MM-DD"
- facility: Healthcare facility name
- insurance_company: Real insurance company (e.g., "Aetna", "Blue Cross Blue Shield", "UnitedHealthcare")
- insurance_member_id: Realistic format (letters and numbers, 10-15 chars)
- insurance_phone: Format "1-XXX-XXX-XXXX"
- cpt_code: 5-digit CPT code (e.g., "99213" for office visit, "12001" for simple wound repair)
- provider_npi: 10-digit NPI number
- provider_name: Doctor's name
- appointment_time: ISO format datetime

Make the data realistic and diverse (different insurance companies, procedures, etc.).

Output format:
<patient_data>
[
  {
    "patient_id": "...",
    "patient_name": "...",
    ...
  },
  ...
]
</patient_data>

Generate exactly """ + str(num_patients) + """ patient records."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract JSON from response
        content = response.content[0].text
        patient_data_str = content[content.find("["):content.rfind("]")+1]
        patient_data = json.loads(patient_data_str)

        return patient_data

    def save_test_cases(
        self,
        test_cases: List[Dict[str, Any]],
        output_file: str,
    ):
        """Save test cases to JSON file."""
        with open(output_file, 'w') as f:
            json.dump(test_cases, f, indent=2)

        print(f"✓ Saved {len(test_cases)} test cases to {output_file}")


# Pre-defined patient data for quick testing
SAMPLE_PATIENT_DATA = {
    "patient_id": "507f1f77bcf86cd799439011",
    "patient_name": "John Smith",
    "date_of_birth": "1985-03-15",
    "facility": "Adam's Medical Practice",
    "insurance_company": "Blue Cross Blue Shield",
    "insurance_member_id": "BCBS123456789",
    "insurance_phone": "1-800-555-0123",
    "cpt_code": "99213",
    "provider_npi": "1234567890",
    "provider_name": "Dr. Sarah Johnson",
    "appointment_time": "2025-11-15T14:30:00",
}
