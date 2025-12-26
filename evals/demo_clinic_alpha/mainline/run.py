"""
Mainline Flow Evaluation Runner

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

from evals.context import EvalContextManager
from evals.db import get_patient_db, ORG_ID_STR

import yaml
from anthropic import Anthropic
from openai import AsyncOpenAI
from langfuse import Langfuse, observe

from clients.demo_clinic_alpha.mainline.flow_definition import MainlineFlow
from clients.demo_clinic_alpha.mainline.schema import WORKFLOW_SCHEMA


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


def grade_routing(conv_text: str, expected_problem: str, calls_text: str, final_state: dict) -> dict:
    """Grade whether the bot routed correctly based on caller intent."""
    routed_to = final_state.get("routed_to", "Unknown")
    call_reason = final_state.get("call_reason", "")
    handed_off_to = final_state.get("handed_off_to", "")

    prompt = f"""Grade this mainline receptionist conversation. Be STRICT but FAIR.

EXPECTED PROBLEM TO CHECK FOR:
{expected_problem}

CONVERSATION:
{conv_text}

FUNCTION CALLS:
{calls_text}

FINAL ROUTING: {routed_to}
CONTEXT PASSED: {call_reason}
HANDED OFF TO: {handed_off_to}

GRADING CRITERIA:
1. ROUTING: Did bot correctly identify caller intent and route appropriately?
   - Scheduling requests → scheduling workflow
   - Lab results requests → lab_results workflow
   - Billing requests → billing staff
   - Simple questions (hours, parking) → answer directly

2. CONTEXT PRESERVATION: Did the handoff include relevant context?
   - Check if route_to_workflow reason contains key details from conversation
   - Context IS preserved if the reason field includes caller's stated details
   - Note: Subsequent database lookups failing is NOT a context loss issue

3. AVOID FALSE NEGATIVES:
   - If route_to_workflow was called with a detailed reason matching conversation, context was preserved
   - Staff transfers after patient lookup fails is CORRECT behavior, not a routing failure
   - Focus on what the mainline bot did, not what happens in downstream workflows
   - If caller had multiple intents (scheduling + labs + billing) and call ends with staff transfer, staff can handle remaining intents
   - If "ALSO NEEDS" appears in context with billing mentioned, and call ends with request_staff, billing IS being handled

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_conversation_quality(conv_text: str, calls_text: str = "") -> dict:
    """Grade conversational quality - natural, efficient, professional."""
    prompt = f"""Grade this conversation's QUALITY. Be STRICT about these issues:

CONVERSATION:
{conv_text}

FUNCTION CALLS (context captured):
{calls_text}

Check for these problems:
1. REPETITION: Bot says same thing multiple times unnecessarily
2. OVER-TALKING: Bot keeps talking after caller says goodbye
3. ROBOTIC: Unnatural phrasing, lists, or overly formal language
4. WRONG INFO: Bot gives incorrect practice information
5. IGNORED REQUEST: Bot doesn't acknowledge caller's stated need (check function call reasons - if the need is captured there, it was NOT ignored)

IMPORTANT - Do NOT fail for:
- Caller repeating themselves (that's caller behavior, not bot behavior)
- Bot transferring quickly when caller's need is clear and captured in function call reason
- Verification questions asked by sub-workflows after handoff (expected behavior)
- Transferring for callback requests (the bot cannot schedule callbacks - staff must handle it, so transferring with "callback" in the reason IS correct)
- Transferring for urgent requests (staff can prioritize urgent callers better than the bot)

Reply with exactly one line:
PASS: <5 words why ok>
or
FAIL: <5 words what went wrong>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_function_calls(calls_text: str, final_state: dict) -> dict:
    """Grade function call correctness - right functions at right times."""
    prompt = f"""Grade whether the bot called functions correctly for a MAINLINE RECEPTIONIST.

FUNCTION CALLS:
{calls_text}

FINAL STATE:
{json.dumps(final_state, indent=2)}

Check for FUNCTION CALL correctness only:
1. route_to_workflow used for scheduling/lab_results/prescription_status intents
2. request_staff used for billing/front_desk/human requests/unclear needs
3. end_call used appropriately when caller says goodbye
4. save_call_info captures volunteered caller information
5. No premature routing before understanding caller's need

IMPORTANT - Do NOT fail for these issues (they are expected in test environment):
- identity_verified=false (no test patient data in database)
- Patient lookup failures
- Database-related issues

Focus on whether the RIGHT functions were called in the RIGHT order.

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

    final_state = final_state or {}

    # Run all graders
    routing = grade_routing(conv_text, expected_problem, calls_text, final_state)
    quality = grade_conversation_quality(conv_text, calls_text)
    functions = grade_function_calls(calls_text, final_state)

    # All must pass
    all_passed = routing["pass"] and quality["pass"] and functions["pass"]

    # Build combined reason
    failures = []
    if not routing["pass"]:
        failures.append(f"routing: {routing['reason']}")
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
            "routing": routing,
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
        print(f"\n  [SIP TRANSFER] → {config.get('toEndPoint')}\n")


# === FLOW RUNNER ===
class FlowRunner:
    def __init__(self, llm_config: dict, cold_transfer_config: dict, practice_info: dict):
        self.mock_flow_manager = MockFlowManager()
        self.mock_pipeline = MockPipeline()
        self.mock_transport = MockTransport()
        self.llm_config = llm_config

        # Build call_data with practice_info from schema
        call_data = {
            "organization_name": "Demo Clinic Alpha",
            "practice_info": practice_info,
        }

        self.flow = MainlineFlow(
            call_data=call_data,
            session_id="eval-session",
            flow_manager=self.mock_flow_manager,
            main_llm=None,
            context_aggregator=None,
            transport=self.mock_transport,
            pipeline=self.mock_pipeline,
            cold_transfer_config=cold_transfer_config,
        )

        self.current_node = self.flow.create_greeting_node()
        self.context = EvalContextManager()
        self.context.set_node(self.current_node)
        self.function_calls = []  # Track all function calls
        self.done = False
        self.handed_off_to = None  # Track workflow handoffs

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

    @observe(name="monica_turn")
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

                # Don't include content when there's a tool call and respond_immediately
                # This prevents spurious "I'll connect you" messages from handoff nodes
                include_content = msg.content if not self.current_node.get("respond_immediately") else None

                self.context.add_tool_call({
                    "content": include_content,
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

                    # Check if this is a handoff to another flow
                    if func_name == "route_to_workflow":
                        workflow = func_args.get("workflow", "unknown")
                        self.handed_off_to = workflow
                        print(f"\n  [HANDOFF] → {workflow} workflow\n")
                        # Continue with the new workflow's node instead of stopping

                    self.current_node = next_node
                    self.context.set_node(next_node)

                    # Process pre_actions on the new node (e.g., tts_say)
                    pre_actions = self.current_node.get("pre_actions") or []
                    for action in pre_actions:
                        if action.get("type") == "tts_say":
                            pre_action_text = action.get("text", "")
                            if pre_action_text:
                                all_content.append(pre_action_text)
                                self.context.add_assistant_message(pre_action_text)

                    # Check if this node ends the conversation
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
    system_prompt = f"""You are a caller to Demo Clinic Alpha's main phone line.

{persona}

Your details (use these when asked):
- Name: {caller['first_name']} {caller['last_name']}
- Phone: {caller['phone']}
- Date of birth: {caller['dob']}
- Email: {caller['email']}

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


@observe(name="mainline_eval")
async def run_simulation(
    scenario: dict,
    llm_config: dict,
    cold_transfer_config: dict,
    practice_info: dict,
) -> dict:
    """Run a single mainline simulation for a scenario."""
    caller = scenario["caller"]
    persona = scenario["persona"]

    # Look up patient from DB by phone
    caller_phone = caller.get("phone", "")
    db = get_patient_db()
    patient = await db.find_patient_by_phone(caller_phone, ORG_ID_STR)

    if patient:
        print(f"  [DB] Found patient: {patient.get('patient_name', patient.get('first_name', ''))}")
    else:
        print(f"  [DB] Patient not found for {caller_phone}")

    runner = FlowRunner(llm_config, cold_transfer_config, practice_info)

    # Bot greeting
    pre_actions = runner.current_node.get("pre_actions") or []
    greeting = pre_actions[0].get("text", "") if pre_actions else ""

    # Add greeting to context
    runner.context.add_assistant_message(greeting)

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

        # Caller responds
        caller_msg = await get_caller_response(
            [{"role": c["role"], "content": c["content"]} for c in conversation],
            caller,
            persona
        )
        print(f"CALLER: {caller_msg}\n")
        conversation.append({"role": "user", "content": caller_msg, "turn": turn})

        # Monica responds
        bot_response = await runner.process_message(caller_msg, turn)
        if bot_response:
            print(f"MONICA: {bot_response}\n")
            conversation.append({"role": "assistant", "content": bot_response, "turn": turn})

    final_state = runner.mock_flow_manager.state

    # Add handoff info to state for grading
    if runner.handed_off_to:
        final_state["handed_off_to"] = runner.handed_off_to

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
            role = "MONICA" if msg["role"] == "assistant" else "CALLER"
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
    """Run a single scenario and save results."""
    scenario = get_scenario(scenario_id)

    # Load config
    services_path = Path(__file__).parent.parent.parent.parent / "clients/demo_clinic_alpha/mainline/services.yaml"
    with open(services_path) as f:
        services = yaml.safe_load(f)

    llm_config = services["services"]["llm"]
    cold_transfer_config = services.get("cold_transfer", {})

    # Get practice_info from schema
    practice_info = WORKFLOW_SCHEMA.get("practice_info", {})

    # Run simulation (trace is created by @observe decorator)
    result = await run_simulation(scenario, llm_config, cold_transfer_config, practice_info)

    # Grade the result (3 graders: routing, quality, functions)
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
    parser = argparse.ArgumentParser(description="Mainline Flow Evaluation")
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
