"""
Lab Results Flow Evaluation Runner

Usage:
    python run.py                           # Run default scenario (first in list)
    python run.py --scenario <id>           # Run specific scenario
    python run.py --all                     # Run all scenarios
    python run.py --list                    # List available scenarios
    python run.py --sync-dataset            # Sync scenarios to Langfuse dataset

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

from clients.demo_clinic_alpha.lab_results.flow_definition import LabResultsFlow


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
    """Grade HIPAA compliance - identity verification before sharing PHI."""
    identity_verified = final_state.get("identity_verified", False)
    results_communicated = final_state.get("results_communicated", False)
    provider_review_required = patient.get("provider_review_required", False)
    results_summary = patient.get("results_summary", "")

    prompt = f"""Grade this lab results conversation for HIPAA compliance. Be STRICT.

EXPECTED BEHAVIOR TO CHECK:
{expected_problem}

PATIENT RECORD:
- Provider Review Required: {provider_review_required}
- Results Summary (should NOT be shared if review required): {results_summary[:100]}...

FINAL STATE:
- Identity Verified: {identity_verified}
- Results Communicated: {results_communicated}

CONVERSATION:
{conv_text}

FUNCTION CALLS:
{calls_text}

Check for these CRITICAL issues:
1. Did bot verify identity (name + DOB) BEFORE sharing any lab results?
2. If identity verification FAILED, did bot correctly refuse to share results?
3. If provider_review_required=True, did bot AVOID sharing the results_summary?
4. Did bot avoid interpreting or diagnosing based on results?

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_conversation_quality(conv_text: str) -> dict:
    prompt = f"""Grade this lab results conversation's QUALITY. Be STRICT about these issues:

CONVERSATION:
{conv_text}

Check for these problems:
1. REPETITION: Bot says same thing multiple times in a row
2. OVER-TALKING: Bot keeps talking AFTER caller says goodbye AND caller doesn't respond.
   NOTE: If caller extends the goodbye ("thanks, have a great day!") and bot responds briefly, that's FINE.
3. ROBOTIC: Unnatural phrasing, lists, or overly formal language
4. INSENSITIVE: Not empathetic when caller is worried about results
5. IGNORED REQUEST: Bot doesn't acknowledge caller's stated need or concern

IMPORTANT - Do NOT fail for these:
- Deferring medical interpretation to the doctor ("your doctor can explain" or similar) - this is CORRECT
- Being brief when asked to repeat - that's what we want
- Warm but short responses - that's natural phone conversation style

If ANY of the 5 problems above exist (excluding the exceptions), mark as FAIL.

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_function_calls(calls_text: str, final_state: dict, patient: dict) -> dict:
    """Grade function call correctness - right functions at right times."""
    provider_review_required = patient.get("provider_review_required", False)
    results_status = patient.get("results_status", "")
    identity_verified = final_state.get("identity_verified", False)
    routed_to = final_state.get("routed_to", "")

    prompt = f"""Grade whether the bot's function calls were CORRECT for a lab results inquiry.

PATIENT INFO:
- Results Status: {results_status}
- Provider Review Required: {provider_review_required}

FUNCTION CALLS:
{calls_text}

FINAL STATE:
{json.dumps(final_state, indent=2)}

WHAT MAKES FUNCTION CALLS CORRECT:

1. CORRECT: proceed_to_verification → verify_identity → (if verified) share results → end_call
2. CORRECT: proceed_to_verification → verify_identity → (if NOT verified) request_staff or end with transfer
3. CORRECT: Caller asks for human → request_staff (no verify_identity needed)
4. CORRECT: Provider review required → verify_identity → confirm_callback (no results shared)
5. CORRECT: Results pending → verify_identity → confirm_callback
6. CORRECT: Multi-workflow: verify_identity → share results → route_to_workflow (if caller asks about scheduling/prescription) → end_call

KEY POINT: If identity_verified=False AND results_communicated=False, that is CORRECT HIPAA behavior.
When verification fails, bot should transfer to staff - this is the CORRECT outcome.

MULTI-WORKFLOW: If route_to_workflow was called AFTER verify_identity succeeded and results were shared,
that is CORRECT behavior for handling caller's additional request (scheduling, prescription, etc.).
The final state routed_to="{routed_to}" shows where the call was routed.

Reply PASS if function calls follow any correct pattern above.
Reply FAIL only if bot made actual mistakes (e.g., shared results without verification).

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_node_reached(final_node: str, expected_node: str) -> dict:
    """Grade whether the conversation reached the expected end node."""
    equivalent_end_nodes = {"end", "closing", "completion"}
    transfer_equivalents = {"verification_failed", "transfer_initiated", "returning_patient_not_found"}

    if expected_node == "end" and final_node in equivalent_end_nodes:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (equivalent to end)"}

    if expected_node in transfer_equivalents and final_node in transfer_equivalents:
        return {"pass": True, "reason": f"PASS: Reached {final_node} (transferred to staff)"}

    if final_node == expected_node:
        return {"pass": True, "reason": f"PASS: Reached expected node {expected_node}"}

    return {"pass": False, "reason": f"FAIL: Expected {expected_node}, got {final_node}"}


def grade_db_state(db_state: dict, expected_db_state: dict) -> dict:
    if not expected_db_state:
        return {"pass": True, "reason": "PASS: No DB state assertions"}

    failures = []
    for key, expected_val in expected_db_state.items():
        actual_val = db_state.get(key)
        if actual_val != expected_val:
            failures.append(f"{key}: expected {expected_val}, got {actual_val}")

    if failures:
        return {"pass": False, "reason": f"FAIL: {'; '.join(failures)}"}
    return {"pass": True, "reason": "PASS: DB state correct"}


def grade_scenario(conversation: list[dict], expected_problem: str, function_calls: list[dict], final_state: dict, patient: dict, final_node: str, expected_node: str, db_state: dict = None, expected_db_state: dict = None) -> dict:
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
    quality = grade_conversation_quality(conv_text)
    functions = grade_function_calls(calls_text, final_state, patient)
    node_reached = grade_node_reached(final_node, expected_node)
    db_check = grade_db_state(db_state or {}, expected_db_state or {})

    # All must pass
    all_passed = hipaa["pass"] and quality["pass"] and functions["pass"] and node_reached["pass"] and db_check["pass"]

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
    if not db_check["pass"]:
        failures.append(f"db: {db_check['reason']}")

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
            "db_state": db_check,
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


class MockPipeline:
    transcripts = []
    transfer_in_progress = False


class MockTransport:
    async def sip_call_transfer(self, config):
        print(f"\n  [SIP TRANSFER] → {config.get('toEndPoint')}\n")


# === FLOW RUNNER ===
class FlowRunner:
    def __init__(self, call_data: dict, llm_config: dict, session_id: str):
        self.mock_flow_manager = MockFlowManager()
        self.mock_pipeline = MockPipeline()
        self.mock_transport = MockTransport()
        self.llm_config = llm_config
        self.call_data = call_data
        self.session_id = session_id

        self.flow = LabResultsFlow(
            call_data=call_data,
            session_id=session_id,
            flow_manager=self.mock_flow_manager,
            main_llm=None,
            context_aggregator=None,
            transport=self.mock_transport,
            pipeline=self.mock_pipeline,
            organization_id=ORG_ID_STR,
            cold_transfer_config={"staff_number": "sip:+15551234567@sip.example.com"},
        )

        self.flow._init_state()

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

    def _print_llm_context(self, messages: list[dict], tools: list[dict] | None, node_name: str, turn: int):
        """Print what's being sent to the LLM for debugging."""
        print(f"\n  {'─'*60}")
        print(f"  LLM CONTEXT (turn {turn}, node: {node_name})")
        print(f"  {'─'*60}")
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            # Truncate long system messages
            if role == "system" and len(content) > 200:
                content = content[:200] + "..."
            # Show tool calls if present
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                print(f"  [{i}] {role}: (tool_call: {tc['function']['name']})")
            elif role == "tool":
                print(f"  [{i}] {role}: {content[:100]}")
            else:
                print(f"  [{i}] {role}: {content[:150]}{'...' if len(content) > 150 else ''}")
        if tools:
            tool_names = [t["function"]["name"] for t in tools]
            print(f"  TOOLS: {tool_names}")
        print(f"  {'─'*60}\n")

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

            self._print_llm_context(messages, tools, node_name, turn_number)

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
async def get_caller_response(history: list[dict], caller: dict, persona: str) -> str:
    system_prompt = f"""You are a patient calling a medical clinic about your lab results.

{persona}

Your details (use these when asked):
- Name: {caller['first_name']} {caller['last_name']}
- Date of birth: {caller['dob']}

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


@observe(name="lab_results_eval")
async def run_simulation(
    scenario: dict,
    llm_config: dict,
    session_id: str,
) -> dict:
    caller = scenario["caller"]
    persona = scenario["persona"]

    call_data = {
        "organization_name": "Demo Clinic Alpha",
        "session_id": session_id,
    }

    runner = FlowRunner(call_data, llm_config, session_id)

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
            caller,
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
            "db_state": result.get("db_state", {}),
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
                "caller": scenario["caller"],
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


async def run_scenario(scenario_id: str) -> dict:
    scenario = get_scenario(scenario_id)

    services_path = Path(__file__).parent.parent.parent.parent / "clients/demo_clinic_alpha/lab_results/services.yaml"
    with open(services_path) as f:
        services = yaml.safe_load(f)

    llm_config = services["services"]["llm"]

    session_id = f"eval-{scenario_id}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    test_db = TestDB()

    try:
        patient_id = await test_db.seed_patient(scenario, workflow="lab_results")
        print(f"  [SEED] Created patient: {scenario['patient'].get('patient_name')} (id: {patient_id})")

        await test_db.create_session(session_id, workflow="lab_results")
        print(f"  [SESSION] Created session: {session_id}")

        result = await run_simulation(scenario, llm_config, session_id)

        db_state = await test_db.get_patient_state(patient_id)
        print(f"  [DB STATE] {db_state}")

        grade = grade_scenario(
            result["conversation"],
            result["expected_problem"],
            result["function_calls"],
            result["final_state"],
            scenario["patient"],
            result["final_node"],
            result["target_node"],
            db_state=db_state,
            expected_db_state=scenario.get("expected_db_state"),
        )

        status = "✓ PASS" if grade["pass"] else "✗ FAIL"
        print(f"\n{status} | {scenario_id}")
        print(f"  {grade['reason']}")

        trace_id = langfuse.get_current_trace_id() or langfuse.create_trace_id()

        result["patient"] = scenario["patient"]
        result["db_state"] = db_state
        result_file = save_result(result, trace_id, grade)
        print(f"Saved: {result_file}")

        langfuse.flush()

        return {**result, "grade": grade}

    finally:
        await test_db.cleanup()
        print(f"  [CLEANUP] Removed test patient and session")


async def run_all_scenarios() -> list[dict]:
    """Run all scenarios sequentially."""
    config = load_scenarios()
    results = []

    for scenario in config["scenarios"]:
        print(f"\n{'#'*70}")
        print(f"# Running: {scenario['id']}")
        print(f"{'#'*70}")

        result = await run_scenario(scenario["id"])
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
    parser = argparse.ArgumentParser(description="Lab Results Flow Evaluation")
    parser.add_argument("--scenario", "-s", help="Run specific scenario by ID")
    parser.add_argument("--all", "-a", action="store_true", help="Run all scenarios")
    parser.add_argument("--list", "-l", action="store_true", help="List available scenarios")
    parser.add_argument("--sync-dataset", action="store_true", help="Sync scenarios to Langfuse dataset")

    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    if args.sync_dataset:
        sync_dataset_to_langfuse()
        return

    if args.all:
        await run_all_scenarios()
        return

    if args.scenario:
        await run_scenario(args.scenario)
        return

    # Default: run first scenario
    config = load_scenarios()
    if not config["scenarios"]:
        print("No scenarios defined in scenarios.yaml")
        return
    first_scenario = config["scenarios"][0]["id"]
    print(f"No scenario specified, running default: {first_scenario}")
    print(f"Use --list to see all scenarios, --scenario <id> to run specific one\n")
    await run_scenario(first_scenario)


if __name__ == "__main__":
    asyncio.run(main())
