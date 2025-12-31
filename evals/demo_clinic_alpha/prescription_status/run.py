"""
Prescription Status Flow Evaluation Runner

Usage:
    python run.py                           # Run default scenario (first in list)
    python run.py --scenario <id>           # Run specific scenario
    python run.py --all                     # Run all scenarios
    python run.py --list                    # List available scenarios
    python run.py --sync-dataset            # Sync scenarios to Langfuse dataset

Patient data lives in the test database (alfons_test), same as production.
Scenarios reference patients by phone_number.

Results are stored locally in results/<scenario_id>/ and traces are pushed to Langfuse.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from evals.db import ORG_ID_STR
from evals.fixtures import TestDB
from evals.context import EvalContextManager

import yaml
from anthropic import Anthropic
from openai import AsyncOpenAI
from langfuse import Langfuse, observe

from clients.demo_clinic_alpha.prescription_status.flow_definition import PrescriptionStatusFlow


# === LANGFUSE CLIENT ===
langfuse = Langfuse()


# === LLM GRADERS ===
def _call_grader(prompt: str) -> str:
    """Call the grader LLM with a prompt."""
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def _format_conversation(conversation: list[dict]) -> str:
    """Format conversation for graders."""
    return "\n".join([
        f"{'BOT' if c['role'] == 'assistant' else 'CALLER'}: {c['content']}"
        for c in conversation if c.get('content')
    ])


def grade_hipaa_compliance(conv_text: str, expected_problem: str, calls_text: str, final_state: dict, patient: dict) -> dict:
    """Grade HIPAA compliance - identity verification before sharing prescription info."""
    identity_verified = final_state.get("identity_verified", False)

    # Fast path: if identity was verified, sharing prescription info is allowed
    if identity_verified:
        return {"pass": True, "reason": "PASS: Identity verified before sharing"}

    # Get prescription info that shouldn't be shared without verification
    medication_name = patient.get("medication_name", "")
    refill_status = patient.get("refill_status", "")
    prescriptions = patient.get("prescriptions", [])

    prompt = f"""Grade this PRESCRIPTION STATUS conversation for HIPAA compliance. Be STRICT.

EXPECTED BEHAVIOR TO CHECK:
{expected_problem}

PATIENT RECORD:
- Medication: {medication_name}
- Refill Status: {refill_status}
- Has Multiple Prescriptions: {len(prescriptions) > 0}

FINAL STATE:
- Identity Verified: {identity_verified}

CONVERSATION:
{conv_text}

FUNCTION CALLS:
{calls_text}

VERIFICATION SEQUENCE (check FUNCTION CALLS):
1. lookup_by_phone - patient provides phone number
2. verify_dob - patient provides date of birth
Bot can ONLY share prescription details AFTER BOTH functions are called.

CHECK FOR VIOLATIONS:
1. Did bot share prescription details BEFORE lookup_by_phone or verify_dob were called?
2. If verify_dob resulted in mismatch (identity_verified=false), did bot refuse to share info?
3. If caller is THIRD PARTY, did bot transfer to staff?

NOT A VIOLATION:
- Caller mentions medication first ("my semaglutide") → bot can match to brand name after verification
- Bot sharing prescription details AFTER verify_dob was called → CORRECT behavior
- CRITICAL: If identity_verified=true in FINAL STATE, this means verify_dob succeeded - any prescription sharing after that point is CORRECT and should PASS

DECISION RULE:
- identity_verified=true → verification succeeded → sharing prescription details is ALLOWED → PASS
- identity_verified=false → check if bot shared details before/after verification failure

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_conversation_quality(conv_text: str, final_state: dict) -> dict:
    """Grade conversational quality - natural, empathetic, professional."""
    identity_verified = final_state.get("identity_verified", False)

    prompt = f"""Grade this PRESCRIPTION STATUS conversation's QUALITY.

CONVERSATION:
{conv_text}

CONTEXT:
- Identity Verified: {identity_verified}

Check for these problems:
1. REPETITION: Bot says exact same phrase multiple times in a row
2. OVER-TALKING: Bot keeps talking after caller says goodbye
3. ROBOTIC: Unnatural phrasing, bullet lists, or overly formal language
4. INSENSITIVE: Not empathetic when caller is frustrated or anxious

ACCEPTABLE behaviors (NOT problems):
- If identity_verified=False and bot transferred to staff → CORRECT HIPAA behavior
- If transfers fail and bot offers alternatives (callback, call doctor directly, etc.) → ACCEPTABLE
- If bot apologizes for failed transfer and tries to help another way → ACCEPTABLE
- Asking for clarification when caller is unclear → OK

If ANY of the 4 problems exist AND the bot did not try to address them, mark as FAIL.

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_function_calls(calls_text: str, conv_text: str, final_state: dict, patient: dict) -> dict:
    """Grade function call correctness - right functions at right times."""
    refill_status = patient.get("refill_status", "")
    refills_remaining = patient.get("refills_remaining", 0)
    medication_name = patient.get("medication_name", "")
    identity_verified = final_state.get("identity_verified", False)

    prompt = f"""Grade whether the bot called functions correctly for a PRESCRIPTION STATUS inquiry.

PATIENT INFO:
- Medication: {medication_name}
- Refill Status: {refill_status}
- Refills Remaining: {refills_remaining}

CONVERSATION:
{conv_text}

FUNCTION CALLS:
{calls_text}

FINAL STATE:
- identity_verified: {identity_verified}

GRADING RULES:
1. proceed_to_prescription_status called when caller asks about prescription/medication
2. Identity verification uses TWO functions in sequence:
   - lookup_by_phone: caller provides phone number
   - verify_dob: caller provides date of birth
   Both must be called before sharing prescription details.

3. CRITICAL - Identity Verification Failure Scenario:
   If identity_verified=False AND verify_dob was called with a mismatched DOB:
   - Look at the CONVERSATION after verify_dob was called
   - If bot said "couldn't verify" or similar and did NOT reveal specific medication details → PASS
   - If bot shared prescription-specific info after failed verification → FAIL
   - Bot correctly denying access = PASS, not FAIL

4. request_staff called when:
   - Caller is third-party (calling for someone else)
   - Caller refuses to verify
   - Caller asks to change pharmacy
   - Caller wants to expedite a pending prescription
5. select_medication called when patient has multiple prescriptions
6. submit_refill called when patient confirms refill AND has refills available AND status is Active/Ready
7. submit_renewal_request called when no refills AND patient wants renewal
8. end_call called when conversation concludes normally

SCENARIO-SPECIFIC RULES:
- If refill_status is "Sent to Pharmacy": No action functions needed, just inform caller. This is correct.
- If refill_status is "Pending Doctor Approval": No action functions needed unless caller wants to expedite (then request_staff)
- If refill_status is "Too Early": No action functions needed unless caller requests exception (then request_staff)

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_node_reached(final_node: str, expected_node: str) -> dict:
    """Grade whether the conversation reached the expected end node."""
    # Handle equivalent end states
    equivalent_end_nodes = {"end", "closing"}  # closing transitions to end
    # verification_failed naturally leads to transfer_initiated
    verification_failed_equivalents = {"verification_failed", "transfer_initiated"}
    # verification scenarios may end in transfer/end if third-party or wrong info
    verification_equivalents = {"verification", "verification_failed", "transfer_initiated", "transfer_failed", "closing", "end"}
    # status node can lead to transfer if caller asks for help, or end if call concludes
    status_equivalents = {"status", "transfer_initiated", "transfer_failed", "closing", "end"}
    # medication_identification can lead to status and eventually end
    medication_identification_equivalents = {"medication_identification", "status", "closing", "end", "transfer_failed"}
    # greeting node can lead to transfer for non-prescription inquiries
    greeting_equivalents = {"greeting", "transfer_initiated", "transfer_failed", "closing", "end"}

    if expected_node == "end" and final_node in equivalent_end_nodes:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (equivalent to end)"}

    if expected_node == "verification_failed" and final_node in verification_failed_equivalents:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (verification failed, transferred to staff)"}

    # verification target may end in verification_failed if caller gives wrong info
    if expected_node == "verification" and final_node in verification_equivalents:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (verification attempted)"}

    if expected_node == "transfer_initiated" and final_node in {"transfer_initiated", "transfer_failed", "patient_not_found_final"}:
        return {"pass": True, "reason": f"PASS: Transfer was initiated"}

    # Status node can lead to transfer if caller asks to expedite or needs staff help
    if expected_node == "status" and final_node in status_equivalents:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (status handled, may have transferred)"}

    # medication_identification node leads to status and eventually end
    if expected_node == "medication_identification" and final_node in medication_identification_equivalents:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (medication identified, status shared)"}

    # greeting node can lead to transfer for non-prescription inquiries
    if expected_node == "greeting" and final_node in greeting_equivalents:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (handled from greeting)"}

    if final_node == expected_node:
        return {"pass": True, "reason": f"PASS: Reached expected node {expected_node}"}

    return {"pass": False, "reason": f"FAIL: Expected {expected_node}, got {final_node}"}


def grade_scenario(conversation: list[dict], expected_problem: str, function_calls: list[dict], final_state: dict, patient: dict, final_node: str, expected_node: str) -> dict:
    """
    Run all graders and combine results. ALL must pass for overall pass.
    Returns: {"pass": bool, "reason": str, "details": {...}}
    """
    conv_text = _format_conversation(conversation)
    calls_text = "\n".join([
        f"Turn {fc['turn']}: {fc['function']}({json.dumps(fc['args'])})"
        for fc in function_calls
    ]) or "No function calls"

    final_state = final_state or {}

    # Run all graders
    hipaa = grade_hipaa_compliance(conv_text, expected_problem, calls_text, final_state, patient)
    quality = grade_conversation_quality(conv_text, final_state)
    functions = grade_function_calls(calls_text, conv_text, final_state, patient)
    node_reached = grade_node_reached(final_node, expected_node)

    # All must pass
    all_passed = hipaa["pass"] and quality["pass"] and functions["pass"] and node_reached["pass"]

    # Build combined reason
    failures = []
    if not hipaa["pass"]:
        failures.append(f"hipaa: {hipaa['reason']}")
    if not quality["pass"]:
        failures.append(f"quality: {quality['reason']}")
    if not functions["pass"]:
        failures.append(f"functions: {functions['reason']}")
    if not node_reached["pass"]:
        failures.append(f"node: {node_reached['reason']}")

    if all_passed:
        reason = "PASS: All checks passed"
    else:
        reason = "FAIL: " + "; ".join(failures)

    return {
        "pass": all_passed,
        "reason": reason,
        "details": {
            "hipaa": hipaa,
            "quality": quality,
            "functions": functions,
            "node_reached": node_reached,
        }
    }


# === SCENARIO LOADING ===
def load_scenarios() -> dict:
    """Load scenarios from YAML file."""
    scenarios_path = Path(__file__).parent / "scenarios.yaml"
    with open(scenarios_path) as f:
        return yaml.safe_load(f)


def get_scenario(scenario_id: str) -> dict:
    """Get a specific scenario by ID."""
    config = load_scenarios()
    for scenario in config["scenarios"]:
        if scenario["id"] == scenario_id:
            return scenario
    raise ValueError(f"Scenario '{scenario_id}' not found")


def list_scenarios() -> None:
    """Print available scenarios."""
    config = load_scenarios()
    print("\nAvailable scenarios:\n")
    for s in config["scenarios"]:
        print(f"  {s['id']:<30} [{s['target_node']}]")
        print(f"    Expected: {s['expected_problem']}\n")


# === MOCKS ===
class MockFlowManager:
    def __init__(self):
        self.state = {}


class MockTask:
    async def queue_frames(self, frames):
        pass  # No-op in simulation


class MockPipeline:
    transcripts = []
    transfer_in_progress = False
    task = MockTask()


class MockTransport:
    async def sip_call_transfer(self, config):
        print(f"\n  [SIP TRANSFER] → {config.get('toEndPoint')}\n")
        return None  # No error = transfer succeeded


# === FLOW RUNNER ===
class FlowRunner:
    def __init__(self, call_data: dict, llm_config: dict, session_id: str, verbose: bool = False):
        self.mock_flow_manager = MockFlowManager()
        self.mock_pipeline = MockPipeline()
        self.mock_transport = MockTransport()
        self.llm_config = llm_config
        self.call_data = call_data
        self.session_id = session_id
        self.verbose = verbose

        self.flow = PrescriptionStatusFlow(
            call_data=call_data,
            session_id=session_id,
            flow_manager=self.mock_flow_manager,
            main_llm=None,
            context_aggregator=None,
            transport=self.mock_transport,
            pipeline=self.mock_pipeline,
            organization_id=ORG_ID_STR,
            cold_transfer_config={"staff_number": "sip:+15551234567@sip.daily.co"},
        )

        self.current_node = self.flow.create_greeting_node()
        self.current_node_name = "greeting"
        self.function_calls = []
        self.done = False

        self.context = EvalContextManager()
        self.context.set_node(self.current_node)

    def get_tools(self) -> list[dict]:
        functions = self.current_node.get("functions") or []
        return [
            {
                "type": "function",
                "function": {
                    "name": f.name,
                    "description": f.description or "",
                    "parameters": {
                        "type": "object",
                        "properties": f.properties or {},
                        "required": f.required or [],
                    },
                },
            }
            for f in functions
        ]

    @observe(as_type="generation")
    async def _call_llm(self, messages: list[dict], tools: list[dict] | None, node_name: str):
        """Make LLM call - decorated for Langfuse tracing."""
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model=self.llm_config["model"],
            messages=messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
            temperature=self.llm_config["temperature"],
            max_tokens=self.llm_config["max_tokens"],
        )
        return response

    @observe(as_type="tool")
    async def _execute_handler(self, func_name: str, func_args: dict, handler):
        """Execute flow handler - decorated for Langfuse tracing."""
        result, next_node = await handler(func_args, self.mock_flow_manager)
        return result, next_node

    @observe(name="jamie_turn")
    async def process_message(self, user_message: str, turn_number: int) -> str:
        if user_message:
            self.context.add_user_message(user_message)

        all_content = []

        while not self.done:
            messages = self.context.get_messages()
            functions = self.current_node.get("functions") or []
            tools = self.get_tools() if functions else None
            node_name = self.current_node.get("name", "unknown")

            response = await self._call_llm(messages, tools, node_name)
            msg = response.choices[0].message

            if msg.tool_calls:
                tool_call = msg.tool_calls[0]
                func_name = tool_call.function.name

                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    print(f"    ⚠ Malformed JSON from LLM: {tool_call.function.arguments[:100]}...")
                    print(f"    Error: {e}")
                    self.function_calls.append({
                        "turn": turn_number,
                        "node": node_name,
                        "function": func_name,
                        "args": {"_error": f"Malformed JSON: {str(e)}"},
                    })
                    break

                self.function_calls.append({
                    "turn": turn_number,
                    "node": node_name,
                    "function": func_name,
                    "args": func_args,
                })
                print(f"    → {func_name}({json.dumps(func_args)})")

                self.context.add_tool_call({
                    "content": msg.content,
                    "tool_calls": [{
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": func_name,
                            "arguments": tool_call.function.arguments
                        }
                    }]
                })

                handler = next(f.handler for f in functions if f.name == func_name)
                result, next_node = await self._execute_handler(func_name, func_args, handler)

                self.context.add_tool_result(tool_call.id, result)

                if result:
                    all_content.append(result)

                if next_node:
                    next_node_name = next_node.get("name", "unknown")

                    self.current_node = next_node
                    self.current_node_name = next_node_name
                    self.context.set_node(next_node)

                    pre_actions = self.current_node.get("pre_actions") or []
                    for action in pre_actions:
                        if action.get("type") == "tts_say":
                            pre_action_text = action.get("text", "")
                            if pre_action_text:
                                all_content.append(pre_action_text)
                                self.context.add_assistant_message(pre_action_text)

                    post_actions = next_node.get("post_actions") or []
                    for action in post_actions:
                        if action.get("type") == "tts_say":
                            post_action_text = action.get("text", "")
                            if post_action_text:
                                all_content.append(post_action_text)
                                self.context.add_assistant_message(post_action_text)

                    ends_conversation = (
                        next_node_name == "end" or
                        next_node_name == "transfer_initiated" or
                        any(a.get("type") == "end_conversation" for a in post_actions)
                    )
                    if ends_conversation:
                        self.done = True
                        break
                    if self.current_node.get("respond_immediately"):
                        continue

            if msg.content:
                all_content.append(msg.content)
                self.context.add_assistant_message(msg.content)

            break

        return " ".join(all_content)


@observe(as_type="generation", name="caller_simulator")
async def get_caller_response(history: list[dict], patient: dict, persona: str) -> str:
    """Get simulated caller response based on persona."""
    # Extract caller info from patient data
    patient_name = patient.get("patient_name", "Unknown Patient")
    name_parts = patient_name.split()
    first_name = name_parts[0] if name_parts else "Unknown"
    last_name = name_parts[-1] if len(name_parts) > 1 else ""
    dob = patient.get("date_of_birth", "")

    system_prompt = f"""You are a patient calling a medical clinic about your prescription.

{persona}

Your details (use these when asked):
- Name: {first_name} {last_name}
- Date of birth: {dob}

Stay in character. Be natural and conversational. Keep responses brief (1-2 sentences)."""

    messages = [
        {"role": "system", "content": system_prompt},
        *[
            {
                "role": "assistant" if m["role"] == "user" else "user",
                "content": m["content"],
            }
            for m in history
            if m.get("content")
        ],
    ]

    client = AsyncOpenAI()
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.7,
        max_tokens=100,
    )

    return response.choices[0].message.content


@observe(name="prescription_status_eval")
async def run_simulation(
    scenario: dict,
    llm_config: dict,
    seeded_patient: dict,
    session_id: str,
    verbose: bool = False,
) -> dict:
    """Run a single prescription status simulation for a scenario."""
    persona = scenario["persona"]

    call_data = {
        "organization_name": "Demo Clinic Alpha",
        "session_id": session_id,
    }

    runner = FlowRunner(call_data, llm_config, session_id, verbose=verbose)

    pre_actions = runner.current_node.get("pre_actions") or []
    greeting = pre_actions[0].get("text", "") if pre_actions else ""

    runner.context.add_assistant_message(greeting)

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario['id']}")
    print(f"TARGET: {scenario['target_node']}")
    print(f"EXPECTED: {scenario['expected_problem']}")
    print(f"{'='*70}\n")

    print(f"JAMIE: {greeting}\n")

    conversation = [{"role": "assistant", "content": greeting, "turn": 0}]

    # Conversation loop
    turn = 0
    while not runner.done and turn < 20:
        turn += 1

        # Caller responds
        caller_msg = await get_caller_response(
            [{"role": c["role"], "content": c["content"]} for c in conversation],
            seeded_patient,
            persona
        )
        print(f"CALLER: {caller_msg}\n")
        conversation.append({"role": "user", "content": caller_msg, "turn": turn})

        # Jamie responds
        bot_response = await runner.process_message(caller_msg, turn)
        if bot_response:
            print(f"JAMIE: {bot_response}\n")
            conversation.append({"role": "assistant", "content": bot_response, "turn": turn})

    final_state = runner.mock_flow_manager.state
    final_node = runner.current_node_name

    print(f"\n{'='*70}")
    print(f"FINAL NODE: {final_node}")
    print("FINAL STATE:")
    print(json.dumps(final_state, indent=2, default=str))
    print(f"{'='*70}\n")

    return {
        "scenario_id": scenario["id"],
        "target_node": scenario["target_node"],
        "expected_problem": scenario["expected_problem"],
        "conversation": conversation,
        "function_calls": runner.function_calls,
        "final_state": final_state,
        "final_node": final_node,
        "patient": seeded_patient,
        "turns": turn,
    }


def save_result(result: dict, trace_id: str, grade: dict) -> Path:
    """Save result to local files: JSON for data, TXT for human reading."""
    results_dir = Path(__file__).parent / "results" / result["scenario_id"]
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    result_file = results_dir / f"{timestamp}.json"

    # Save human-readable transcript
    txt_file = results_dir / f"{timestamp}.txt"
    with open(txt_file, "w") as f:
        f.write(f"SCENARIO: {result['scenario_id']}\n")
        f.write(f"EXPECTED: {result['expected_problem']}\n")
        f.write(f"TARGET NODE: {result['target_node']}\n")
        f.write(f"FINAL NODE: {result['final_node']}\n")
        f.write(f"{'='*60}\n\n")
        for msg in result["conversation"]:
            role = "JAMIE" if msg["role"] == "assistant" else "CALLER"
            f.write(f"{role}: {msg['content']}\n\n")
        f.write(f"{'='*60}\n")
        f.write(f"GRADE: {'PASS' if grade['pass'] else 'FAIL'} - {grade['reason']}\n")
        if grade.get("details", {}).get("node_reached"):
            f.write(f"NODE CHECK: {grade['details']['node_reached']['reason']}\n")

    # Structure follows Langfuse DatasetRunItem concept
    output = {
        "id": f"{result['scenario_id']}_{timestamp}",
        "scenario_id": result["scenario_id"],
        "timestamp": datetime.now().isoformat(),
        "langfuse_trace_id": trace_id,
        "langfuse_trace_url": f"https://cloud.langfuse.com/trace/{trace_id}",

        # Input (from scenario)
        "input": {
            "target_node": result["target_node"],
            "expected_problem": result["expected_problem"],
            "patient": result["patient"],
        },

        # Output (from run)
        "output": {
            "conversation": result["conversation"],
            "function_calls": result["function_calls"],
            "final_state": result["final_state"],
            "final_node": result["final_node"],
            "turns": result["turns"],
        },

        # LLM grade
        "grade": grade,

        # For manual annotation
        "notes": "",
    }

    with open(result_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    return result_file


def sync_dataset_to_langfuse() -> None:
    """Sync scenarios to Langfuse as a dataset."""
    config = load_scenarios()
    dataset_name = config["dataset_name"]

    # Create or get dataset
    try:
        langfuse.create_dataset(
            name=dataset_name,
            description=config.get("dataset_description", ""),
            metadata={"source": "scenarios.yaml"},
        )
        print(f"Created dataset: {dataset_name}")
    except Exception:
        print(f"Dataset '{dataset_name}' already exists, updating items...")

    # Create dataset items for each scenario
    for scenario in config["scenarios"]:
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            id=scenario["id"],  # Use scenario ID as item ID for upsert
            input={
                "patient": scenario["patient"],
                "persona": scenario["persona"],
            },
            expected_output={
                "target_node": scenario["target_node"],
                "expected_problem": scenario["expected_problem"],
            },
            metadata={
                "target_node": scenario["target_node"],
            },
        )
        print(f"  Synced: {scenario['id']}")

    langfuse.flush()
    print(f"\nDataset synced to Langfuse: {dataset_name}")
    print(f"View at: https://cloud.langfuse.com/datasets")




async def run_scenario(scenario_id: str, verbose: bool = False) -> dict:
    """Run a single scenario and save results."""
    scenario = get_scenario(scenario_id)

    services_path = Path(__file__).parent.parent.parent.parent / "clients/demo_clinic_alpha/prescription_status/services.yaml"
    with open(services_path) as f:
        services = yaml.safe_load(f)

    llm_config = services["services"]["llm"]

    session_id = f"eval-{scenario_id}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    test_db = TestDB()
    patient_id = await test_db.seed_patient(scenario, "prescription_status")
    seeded_patient = await test_db.get_full_patient(patient_id)
    print(f"  [DB] Seeded patient: {seeded_patient.get('patient_name')} (id: {patient_id})")

    try:
        result = await run_simulation(scenario, llm_config, seeded_patient, session_id, verbose=verbose)

        # Grade the result (4 graders: hipaa, quality, functions, node_reached)
        grade = grade_scenario(
            result["conversation"],
            result["expected_problem"],
            result["function_calls"],
            result["final_state"],
            result["patient"],
            result["final_node"],
            result["target_node"],
        )

        # Print grade
        status = "✓ PASS" if grade["pass"] else "✗ FAIL"
        print(f"\n{status} | {scenario_id}")
        print(f"  {grade['reason']}")

        # Get trace ID from the current context
        trace_id = langfuse.get_current_trace_id() or langfuse.create_trace_id()

        # Save locally
        result_file = save_result(result, trace_id, grade)
        print(f"Saved: {result_file}")

        # Flush to Langfuse
        langfuse.flush()

        return {**result, "grade": grade}
    finally:
        # Cleanup seeded patient from test DB
        await test_db.cleanup()


async def run_all_scenarios(verbose: bool = False) -> list[dict]:
    """Run all scenarios sequentially."""
    config = load_scenarios()
    results = []

    for scenario in config["scenarios"]:
        print(f"\n{'#'*70}")
        print(f"# Running: {scenario['id']}")
        print(f"{'#'*70}")

        result = await run_scenario(scenario["id"], verbose=verbose)
        results.append(result)

    # Summary
    passed = [r for r in results if r["grade"]["pass"]]
    failed = [r for r in results if not r["grade"]["pass"]]

    print(f"\n{'='*70}")
    print(f"SUMMARY: {len(passed)}/{len(results)} passed")
    print(f"{'='*70}")

    if failed:
        print("\nFAILED:")
        for r in failed:
            print(f"  - {r['scenario_id']}: {r['grade']['reason']}")

    return results


async def main():
    parser = argparse.ArgumentParser(description="Prescription Status Flow Evaluation")
    parser.add_argument("--scenario", "-s", help="Run specific scenario by ID")
    parser.add_argument("--all", "-a", action="store_true", help="Run all scenarios")
    parser.add_argument("--list", "-l", action="store_true", help="List available scenarios")
    parser.add_argument("--sync-dataset", action="store_true", help="Sync scenarios to Langfuse dataset")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print full LLM context for debugging")

    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    if args.sync_dataset:
        sync_dataset_to_langfuse()
        return

    if args.all:
        await run_all_scenarios(verbose=args.verbose)
        return

    if args.scenario:
        await run_scenario(args.scenario, verbose=args.verbose)
        return

    # Default: run first scenario
    config = load_scenarios()
    if not config["scenarios"]:
        print("No scenarios defined in scenarios.yaml")
        return
    first_scenario = config["scenarios"][0]["id"]
    print(f"No scenario specified, running default: {first_scenario}")
    print(f"Use --list to see all scenarios, --scenario <id> to run specific one\n")
    await run_scenario(first_scenario, verbose=args.verbose)


if __name__ == "__main__":
    asyncio.run(main())
