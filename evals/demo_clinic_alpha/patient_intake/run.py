"""
Patient Intake Flow Evaluation Runner

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

import yaml
from anthropic import Anthropic
from openai import AsyncOpenAI
from langfuse import Langfuse, observe

from clients.demo_clinic_alpha.patient_intake.flow_definition import PatientIntakeFlow


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
        f"{'BOT' if c['role'] == 'assistant' else 'PATIENT'}: {c['content']}"
        for c in conversation if c.get('content')
    ])


def grade_goal(conv_text: str, expected_problem: str, calls_text: str) -> dict:
    """Grade whether the scenario's expected problem occurred."""
    prompt = f"""Grade this patient intake conversation. Be STRICT.

EXPECTED PROBLEM TO CHECK FOR:
{expected_problem}

CONVERSATION:
{conv_text}

FUNCTION CALLS:
{calls_text}

Did the expected problem occur? Look for:
- Bot confusion, loops, or wrong assumptions
- Missing information, wrong data captured
- Poor handling of the patient's behavior

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_conversation_quality(conv_text: str) -> dict:
    """Grade conversational quality - no repetition, natural flow."""
    prompt = f"""Grade this conversation's QUALITY. Be STRICT about these issues:

CONVERSATION:
{conv_text}

Check for these problems:
1. REPETITION: Bot says same thing multiple times (e.g., multiple "Goodbye", repeated confirmations)
2. OVER-TALKING: Bot keeps talking after patient says goodbye
3. ROBOTIC: Unnatural phrasing, lists, or overly formal language
4. RAMBLING: Unnecessarily long responses when brief would work

If ANY of these issues exist, mark as FAIL.

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_function_calls(calls_text: str, final_state: dict) -> dict:
    """Grade function call correctness - right functions, right data."""
    prompt = f"""Grade whether the bot called functions correctly.

FUNCTION CALLS:
{calls_text}

FINAL STATE:
{json.dumps(final_state, indent=2)}

Check for:
1. Called functions at appropriate times (not prematurely)
2. Captured data correctly (no typos, wrong values)
3. Didn't skip required functions
4. Didn't call unnecessary functions

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_scenario(conversation: list[dict], expected_problem: str, function_calls: list[dict], final_state: dict = None) -> dict:
    """
    Run all graders and combine results. ALL must pass for overall pass.
    Returns: {"pass": bool, "reason": str, "details": {...}}
    """
    conv_text = _format_conversation(conversation)
    calls_text = "\n".join([
        f"Turn {fc['turn']}: {fc['function']}({json.dumps(fc['args'])})"
        for fc in function_calls
    ]) or "No function calls"

    # Run all graders
    goal = grade_goal(conv_text, expected_problem, calls_text)
    quality = grade_conversation_quality(conv_text)
    functions = grade_function_calls(calls_text, final_state or {})

    # All must pass
    all_passed = goal["pass"] and quality["pass"] and functions["pass"]

    # Build combined reason
    failures = []
    if not goal["pass"]:
        failures.append(f"goal: {goal['reason']}")
    if not quality["pass"]:
        failures.append(f"quality: {quality['reason']}")
    if not functions["pass"]:
        failures.append(f"functions: {functions['reason']}")

    if all_passed:
        reason = "PASS: All checks passed"
    else:
        reason = "FAIL: " + "; ".join(failures)

    return {
        "pass": all_passed,
        "reason": reason,
        "details": {
            "goal": goal,
            "quality": quality,
            "functions": functions,
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
        print(f"\n  [TRANSFER] → {config.get('toEndPoint')}\n")


# === FLOW RUNNER ===
class FlowRunner:
    def __init__(self, llm_config: dict, cold_transfer_config: dict):
        self.mock_flow_manager = MockFlowManager()
        self.mock_pipeline = MockPipeline()
        self.mock_transport = MockTransport()
        self.llm_config = llm_config

        self.flow = PatientIntakeFlow(
            patient_data={"organization_name": "Demo Clinic Alpha"},
            flow_manager=self.mock_flow_manager,
            main_llm=None,
            context_aggregator=None,
            transport=self.mock_transport,
            pipeline=self.mock_pipeline,
            cold_transfer_config=cold_transfer_config,
        )

        self.current_node = self.flow.create_greeting_node()
        self.conversation_history = []
        self.function_calls = []  # Track all function calls
        self.done = False

    def get_prompts(self) -> list[dict]:
        messages = []
        role_msgs = self.current_node.get("role_messages") or []
        task_msgs = self.current_node.get("task_messages") or []
        messages.extend(role_msgs)
        messages.extend(task_msgs)
        return messages

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
            tools=tools,
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

    @observe(name="monica_turn")
    async def process_message(self, user_message: str, turn_number: int) -> str:
        if user_message:
            self.conversation_history.append({"role": "user", "content": user_message})

        all_content = []

        while not self.done:
            messages = self.get_prompts() + self.conversation_history
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
                    # Record the failure and continue without executing the function
                    self.function_calls.append({
                        "turn": turn_number,
                        "node": node_name,
                        "function": func_name,
                        "args": {"_error": f"Malformed JSON: {str(e)}"},
                    })
                    break

                # Track function call
                self.function_calls.append({
                    "turn": turn_number,
                    "node": node_name,
                    "function": func_name,
                    "args": func_args,
                })
                print(f"    → {func_name}({json.dumps(func_args)})")

                self.conversation_history.append({
                    "role": "assistant",
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

                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result or "OK"
                })

                if result:
                    all_content.append(result)

                if next_node:
                    self.current_node = next_node
                    # Process pre_actions on the new node (e.g., tts_say)
                    pre_actions = self.current_node.get("pre_actions") or []
                    for action in pre_actions:
                        if action.get("type") == "tts_say":
                            pre_action_text = action.get("text", "")
                            if pre_action_text:
                                all_content.append(pre_action_text)
                                self.conversation_history.append({
                                    "role": "assistant",
                                    "content": pre_action_text
                                })

                    # Check if this node ends the conversation
                    result_node_name = next_node.get("name")
                    post_actions = next_node.get("post_actions") or []
                    ends_conversation = (
                        result_node_name == "end" or
                        any(a.get("type") == "end_conversation" for a in post_actions)
                    )
                    if ends_conversation:
                        self.done = True
                        break
                    if self.current_node.get("respond_immediately"):
                        continue

            if msg.content:
                all_content.append(msg.content)
                self.conversation_history.append({"role": "assistant", "content": msg.content})

            break

        return " ".join(all_content)


@observe(as_type="generation", name="patient_simulator")
async def get_patient_response(history: list[dict], patient: dict, persona: str) -> str:
    system_prompt = f"""You are a patient calling Demo Clinic Alpha.

{persona}

Your details (use these when asked):
- Name: {patient['first_name']} {patient['last_name']}
- Phone: {patient['phone']}
- Date of birth: {patient['dob']}
- Email: {patient['email']}

Stay in character. Be natural and conversational."""

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
        max_tokens=150,
    )

    return response.choices[0].message.content


@observe(name="patient_intake_eval")
async def run_simulation(
    scenario: dict,
    llm_config: dict,
    cold_transfer_config: dict,
) -> dict:
    """Run a single patient intake simulation for a scenario."""
    patient = scenario["patient"]
    persona = scenario["persona"]

    runner = FlowRunner(llm_config, cold_transfer_config)

    # Bot greeting
    pre_actions = runner.current_node.get("pre_actions") or []
    greeting = pre_actions[0].get("text", "") if pre_actions else ""

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario['id']}")
    print(f"TARGET: {scenario['target_node']}")
    print(f"EXPECTED PROBLEM: {scenario['expected_problem']}")
    print(f"{'='*70}\n")

    print(f"MONICA: {greeting}\n")

    conversation = [{"role": "assistant", "content": greeting, "turn": 0}]

    # Conversation loop
    turn = 0
    while not runner.done and turn < 20:
        turn += 1

        # Patient responds
        patient_msg = await get_patient_response(
            [{"role": c["role"], "content": c["content"]} for c in conversation],
            patient,
            persona
        )
        print(f"PATIENT: {patient_msg}\n")
        conversation.append({"role": "user", "content": patient_msg, "turn": turn})

        # Monica responds
        bot_response = await runner.process_message(patient_msg, turn)
        if bot_response:
            print(f"MONICA: {bot_response}\n")
            conversation.append({"role": "assistant", "content": bot_response, "turn": turn})

    final_state = runner.mock_flow_manager.state

    print(f"\n{'='*70}")
    print("FINAL STATE:")
    print(json.dumps(final_state, indent=2))
    print(f"{'='*70}\n")

    return {
        "scenario_id": scenario["id"],
        "target_node": scenario["target_node"],
        "expected_problem": scenario["expected_problem"],
        "conversation": conversation,
        "function_calls": runner.function_calls,
        "final_state": final_state,
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
        f.write(f"{'='*60}\n\n")
        for msg in result["conversation"]:
            role = "MONICA" if msg["role"] == "assistant" else "PATIENT"
            f.write(f"{role}: {msg['content']}\n\n")
        f.write(f"{'='*60}\n")
        f.write(f"GRADE: {'PASS' if grade['pass'] else 'FAIL'} - {grade['reason']}\n")

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
        },

        # Output (from run)
        "output": {
            "conversation": result["conversation"],
            "function_calls": result["function_calls"],
            "final_state": result["final_state"],
            "turns": result["turns"],
        },

        # LLM grade
        "grade": grade,

        # For manual annotation
        "notes": "",
    }

    with open(result_file, "w") as f:
        json.dump(output, f, indent=2)

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


async def run_scenario(scenario_id: str) -> dict:
    """Run a single scenario and save results."""
    scenario = get_scenario(scenario_id)

    # Load config
    services_path = Path(__file__).parent.parent.parent.parent / "clients/demo_clinic_alpha/patient_intake/services.yaml"
    with open(services_path) as f:
        services = yaml.safe_load(f)

    llm_config = services["services"]["llm"]
    cold_transfer_config = services.get("cold_transfer", {})

    # Run simulation (trace is created by @observe decorator)
    result = await run_simulation(scenario, llm_config, cold_transfer_config)

    # Grade the result (3 graders: goal, quality, functions)
    grade = grade_scenario(
        result["conversation"],
        result["expected_problem"],
        result["function_calls"],
        result["final_state"]
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
    parser = argparse.ArgumentParser(description="Patient Intake Flow Evaluation")
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
    first_scenario = config["scenarios"][0]["id"]
    print(f"No scenario specified, running default: {first_scenario}")
    print(f"Use --list to see all scenarios, --scenario <id> to run specific one\n")
    await run_scenario(first_scenario)


if __name__ == "__main__":
    asyncio.run(main())
