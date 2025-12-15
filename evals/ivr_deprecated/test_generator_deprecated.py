import os
from typing import List, Dict, Any
from anthropic import Anthropic
import json
from dotenv import load_dotenv

load_dotenv()


class IVRTestGenerator:
    def __init__(self, model: str = "claude-sonnet-4-5"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def generate_test_cases(self, num_cases: int = 5) -> List[Dict[str, Any]]:
        prompt = """You will be generating realistic IVR (Interactive Voice Response) navigation test cases for testing an AI healthcare assistant that calls insurance companies.

**BACKGROUND AND CONTEXT:**

IVR systems are automated phone menus that use voice prompts and DTMF (touch-tone) inputs to route callers. Your test cases will simulate realistic insurance company phone menus.

**CRITICAL CONTEXT - YOU ARE CALLING AS A PROVIDER:**
The AI being tested represents a healthcare office staff member (provider representative), NOT a patient or insurance member. The goal is to navigate insurance company phone systems to reach departments that serve healthcare providers, specifically for:
- Eligibility verification
- Prior authorization requests
- Benefits verification
- Provider-related inquiries

**TARGET DEPARTMENTS (Priority Order):**
1. **Provider Services / Provider Relations** (HIGHEST PRIORITY - this is the correct department for providers)
2. **Healthcare Provider line** (HIGH PRIORITY)
3. **Speak to Representative / Agent** (HIGH PRIORITY - reaching a human agent)
4. **Eligibility Verification / Benefits Verification / Prior Authorization** (MEDIUM PRIORITY - acceptable if clearly for providers)
5. **Operator / Press 0** (LAST RESORT - when no clear provider option exists)

**DEPARTMENTS TO AVOID (Wrong for providers):**
- Member Services (for patients/insurance members only)
- Claims / Billing (not the goal for eligibility checks)
- Pharmacy Services
- Find a Doctor

**OUTPUT FORMAT:**

Generate exactly """ + str(num_cases) + """ test cases. Each test case must be valid JSON following this exact structure:

```json
{
  "scenario_id": "provider_eligibility_3step",
  "description": "Navigation from main menu to provider eligibility verification",
  "steps": [
    {
      "user_utterance": "Thank you for calling Blue Cross Insurance. For member services, press 1. For provider services, press 2. For claims, press 3. To hear this menu again, press 9.",
      "expected_dtmf": "2",
      "reasoning": "Selecting provider services as we are calling as a healthcare provider, not a patient"
    },
    {
      "user_utterance": "Provider services. For eligibility verification, press 1. For claims status, press 2. For prior authorizations, press 3. To speak with a representative, press 0.",
      "expected_dtmf": "1",
      "reasoning": "Selecting eligibility verification which is our target department"
    },
    {
      "user_utterance": "Connecting you to eligibility verification. Please hold while we transfer your call.",
      "expected_dtmf": "",
      "reasoning": "No action needed - call is being transferred to a representative"
    }
  ]
}
```

**FIELD SPECIFICATIONS:**

- **scenario_id**: Use snake_case naming (e.g., "prior_auth_4step", "eligibility_dead_end"). Make each ID unique and descriptive.
- **description**: Brief summary of what the navigation accomplishes. Do NOT include step numbers (code adds those automatically).
- **steps**: Array of 2-5 navigation steps. Each step:
  - **user_utterance**: Complete IVR prompt with realistic insurance company language
  - **expected_dtmf**: Single character "0"-"9", "*", "#", or "" (empty when no action needed)
  - **reasoning**: Why this DTMF choice is correct for reaching Provider Services

**CREATING REALISTIC IVR PROMPTS:**

1. **Opening prompts**: Include company greeting, optional quality notices, optional language selection
2. **Menu prompts**: List 3-5 options with realistic terminology, include helpers like "press * to return"
3. **Final prompts**: Indicate success ("Connecting you to...", "Please hold...")
4. **Vary companies**: Blue Cross, Aetna, United Healthcare, Cigna, Humana, etc.
5. **Realistic departments**: Provider Services, Eligibility Verification, Prior Authorization, etc.

**SCENARIO VARIETY REQUIREMENTS:**

Distribute test cases across these categories:

1. **Successful straight-path (40-50% of cases)**: 2-3 steps, direct navigation to target
2. **Dead-end with backtracking (20-30% of cases)**: 3-5 steps, wrong option → press * → correct option
3. **Multi-level deep menus (20-30% of cases)**: 4-5 steps, multiple navigation layers
4. **Vary menu structures**: Different option counts, ordering, companies, with/without language selection

**IMPORTANT RULES:**

1. Never number steps in descriptions
2. Provider perspective only - avoid Member Services unless correcting error
3. All sequences should reach or attempt to reach target
4. Empty DTMF ("") for terminal transfer messages
5. Valid JSON with proper escaping

Output your response as a JSON array inside <test_cases> tags:

<test_cases>
[
  { test case 1 },
  { test case 2 },
  ...
]
</test_cases>"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.content[0].text
        test_cases_str = content[content.find("["):content.rfind("]")+1]
        return json.loads(test_cases_str)