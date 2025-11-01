import os
from typing import List, Dict, Any
from anthropic import Anthropic
import json


class IVRTestGenerator:
    def __init__(self, model: str = "claude-sonnet-4-5"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def generate_single_step_cases(self, num_cases: int = 5) -> List[Dict[str, Any]]:
        prompt = """Generate single-step IVR menu test cases.

CONTEXT:
AI navigates phone menus using DTMF tones.
Single interaction: hear menu, press button.

TASK:
Generate diverse single-step menu scenarios.

For each test case provide:
1. scenario_id: Short identifier
2. description: What this tests
3. user_utterance: Menu announcement
4. expected_behavior: What to do and why

Include:
- Clear provider services option
- Implicit provider option (eligibility, benefits)
- No direct match (need representative)
- Confusing member vs provider options

Output format:
<test_cases>
[
  {
    "scenario_id": "...",
    "description": "...",
    "user_utterance": "...",
    "expected_behavior": "..."
  }
]
</test_cases>

Generate exactly """ + str(num_cases) + """ cases."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        test_cases_str = content[content.find("["):content.rfind("]")+1]
        return json.loads(test_cases_str)

    def generate_multi_step_cases(self, num_cases: int = 3) -> List[Dict[str, Any]]:
        prompt = """Generate multi-step IVR navigation sequences (2-4 steps).

CONTEXT:
AI navigates multi-level phone menus.
Each step: hear menu, press button, get next menu.

TASK:
Generate realistic multi-step navigation flows.

For each test case provide:
1. scenario_id: Short identifier
2. description: What this tests
3. steps: Array of navigation steps

Each step has:
- user_utterance: Menu prompt
- expected_dtmf: Which button to press
- reasoning: Why this choice

Include scenarios:
- 2 steps: Main menu → Provider submenu
- 3 steps: Main → Provider → Prior auth
- 4 steps: Main → Provider → Prior auth → New request
- Final step should reach human or require decision

Output format:
<test_cases>
[
  {
    "scenario_id": "...",
    "description": "...",
    "steps": [
      {
        "user_utterance": "...",
        "expected_dtmf": "2",
        "reasoning": "..."
      }
    ]
  }
]
</test_cases>

Generate exactly """ + str(num_cases) + """ multi-step sequences."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=6000,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        test_cases_str = content[content.find("["):content.rfind("]")+1]
        return json.loads(test_cases_str)

    def generate_dead_end_cases(self, num_cases: int = 2) -> List[Dict[str, Any]]:
        prompt = """Generate IVR dead-end and backtracking scenarios.

CONTEXT:
AI navigates phone menus but sometimes reaches wrong department.
Must recognize dead end and backtrack using * or 0.

TASK:
Generate scenarios requiring backtracking.

For each test case provide:
1. scenario_id: Short identifier
2. description: What this tests
3. steps: Array showing wrong path then correction

Include:
- Wrong submenu selected, need to press * to go back
- Member services instead of provider, need to backtrack
- Claims department instead of eligibility, press 0 for operator
- Multi-level wrong path requiring multiple backtracks

Output format:
<test_cases>
[
  {
    "scenario_id": "...",
    "description": "...",
    "steps": [
      {
        "user_utterance": "...",
        "expected_dtmf": "1",
        "reasoning": "..."
      }
    ]
  }
]
</test_cases>

Generate exactly """ + str(num_cases) + """ dead-end scenarios."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=5000,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        test_cases_str = content[content.find("["):content.rfind("]")+1]
        return json.loads(test_cases_str)


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
