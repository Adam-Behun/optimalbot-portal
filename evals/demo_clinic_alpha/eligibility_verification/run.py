"""
Eligibility Verification Flow Evaluation Runner

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
from langfuse import Langfuse, observe
from openai import AsyncOpenAI

from clients.demo_clinic_alpha.eligibility_verification.flow_definition import (
    EligibilityVerificationFlow,
)
from evals.context import EvalContextManager
from evals.db import ORG_ID_STR
from evals.fixtures import TestDB

# === LANGFUSE CLIENT ===
langfuse = Langfuse()


# === GRADERS ===
def _call_grader(prompt: str) -> str:
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def _format_conversation(conversation: list[dict]) -> str:
    return "\n".join([
        f"{'BOT' if c['role'] == 'assistant' else 'INSURANCE'}: {c['content']}"
        for c in conversation if c.get('content')
    ])


def _normalize_amount(value) -> str:
    """Normalize amounts: "$50" -> "50.00", "None" -> "none"."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"

    s = str(value).strip().lower()
    if s in ("none", "null", "n/a", ""):
        return "none"

    s = s.replace("$", "").replace("%", "").strip()
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return s


def _values_match(expected, actual) -> bool:
    norm_expected = _normalize_value(expected)
    norm_actual = _normalize_value(actual)
    if norm_expected == norm_actual:
        return True

    amt_expected = _normalize_amount(expected)
    amt_actual = _normalize_amount(actual)
    if amt_expected == amt_actual:
        return True

    if isinstance(expected, str) and isinstance(actual, str):
        try:
            if float(expected) == float(actual):
                return True
        except (ValueError, TypeError):
            pass
    return False


def grade_data_accuracy(conv_text: str, final_state: dict, calls_text: str, expected_data: dict) -> dict:
    if not expected_data:
        return {"pass": True, "reason": "PASS: No expected data defined"}

    failures = []
    matches = []
    for field, expected in expected_data.items():
        actual = final_state.get(field)
        if _values_match(expected, actual):
            matches.append(field)
        else:
            failures.append(f"{field}: expected '{expected}', got '{actual}'")

    if failures:
        return {
            "pass": False,
            "reason": f"FAIL: {'; '.join(failures[:3])}" + (f" (+{len(failures)-3} more)" if len(failures) > 3 else ""),
            "failures": failures,
            "matches": matches,
        }
    return {"pass": True, "reason": f"PASS: All {len(matches)} fields match"}


def grade_node_reached(final_node: str, expected_node: str) -> dict:
    """Grade whether conversation reached expected end node."""
    equivalent_nodes = {
        "closing": {"closing", "end", "completion"},
        "transfer_initiated": {"transfer_initiated", "transfer_pending", "staff_confirmation"},
    }

    if expected_node in equivalent_nodes:
        if final_node in equivalent_nodes[expected_node]:
            return {"pass": True, "reason": f"PASS: Reached {final_node}"}

    if final_node == expected_node:
        return {"pass": True, "reason": f"PASS: Reached {expected_node}"}

    return {"pass": False, "reason": f"FAIL: Expected {expected_node}, got {final_node}"}


def _normalize_value(value):
    """Normalize values for comparison (Yes/No → bool, case-insensitive strings)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.lower().strip()
        if lower in ("yes", "true"):
            return True
        if lower in ("no", "false", "none"):
            return False
        return lower
    return value


def grade_captured_state(final_state: dict, expected_state: dict) -> dict:
    """Grade whether captured state matches expectations."""
    if not expected_state:
        return {"pass": True, "reason": "PASS: No state assertions"}

    failures = []
    for key, expected in expected_state.items():
        actual = final_state.get(key)
        # Normalize both for comparison
        norm_actual = _normalize_value(actual)
        norm_expected = _normalize_value(expected)
        if norm_actual != norm_expected:
            failures.append(f"{key}: expected {expected}, got {actual}")

    if failures:
        return {"pass": False, "reason": f"FAIL: {'; '.join(failures)}"}
    return {"pass": True, "reason": "PASS: Captured state correct"}


def grade_conversation_quality(conv_text: str) -> dict:
    prompt = f"""Grade this conversation's STYLE (not logic). Check ONLY:

CONVERSATION:
{conv_text}

FAIL if:
1. REPETITION: Bot says same phrase multiple times (e.g., multiple "Goodbye")
2. OVER-TALKING: Bot speaks after saying goodbye
3. ROBOTIC: Uses bullet points, lists, or markdown
4. RAMBLING: Responses longer than needed

DO NOT judge the conversation logic or what questions the bot asks.

Reply exactly: PASS: <5 words> or FAIL: <5 words>"""

    result = _call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_forbidden_phrases(conversation: list[dict], forbidden_phrases: list[str]) -> dict:
    """Check that bot never said any of the forbidden phrases."""
    if not forbidden_phrases:
        return {"pass": True, "reason": "PASS: No forbidden phrases defined"}

    # Get all bot messages
    bot_messages = [
        msg["content"].lower()
        for msg in conversation
        if msg.get("role") == "assistant" and msg.get("content")
    ]
    bot_text = " ".join(bot_messages)

    violations = []
    for phrase in forbidden_phrases:
        if phrase.lower() in bot_text:
            violations.append(phrase)

    if violations:
        return {
            "pass": False,
            "reason": f"FAIL: Bot said forbidden phrase(s): {', '.join(violations[:3])}",
            "violations": violations,
        }
    return {"pass": True, "reason": f"PASS: None of {len(forbidden_phrases)} forbidden phrases used"}


def grade_function_calls(
    function_calls: list[dict],
    final_state: dict,
    expected_data: dict = None,
    expected_functions: list[str] = None,
    forbidden_functions: list[str] = None,
) -> dict:
    called_functions = [fc["function"] for fc in function_calls]
    failures = []
    checks = []

    if expected_functions:
        for fn in expected_functions:
            if fn in called_functions:
                checks.append(f"{fn}: called")
            else:
                failures.append(f"Missing: {fn}")

    if forbidden_functions:
        for fn in forbidden_functions:
            if fn in called_functions:
                failures.append(f"Forbidden called: {fn}")

    # function name -> (state_field, arg_name)
    func_to_field = {
        "record_network_status": ("network_status", "status"),
        "record_plan_type": ("plan_type", "plan_type"),
        "record_cpt_covered": ("cpt_covered", "covered"),
        "record_copay": ("copay_amount", "amount"),
        "record_coinsurance": ("coinsurance_percent", "percent"),
        "record_prior_auth_required": ("prior_auth_required", "required"),
        "record_deductible_family": ("deductible_family", "amount"),
        "record_deductible_family_met": ("deductible_family_met", "amount"),
        "record_oop_max_family": ("oop_max_family", "amount"),
        "record_oop_max_family_met": ("oop_max_family_met", "amount"),
        "record_deductible_individual": ("deductible_individual", "amount"),
        "record_deductible_individual_met": ("deductible_individual_met", "amount"),
        "record_oop_max_individual": ("oop_max_individual", "amount"),
        "record_oop_max_individual_met": ("oop_max_individual_met", "amount"),
        "record_reference_number": ("reference_number", "reference_number"),
    }

    # Fields that can be captured via proceed_to_* functions (arg name = state field name)
    transition_fields = {
        "network_status", "plan_type", "cpt_covered", "copay_amount", "coinsurance_percent",
        "prior_auth_required", "telehealth_covered", "deductible_applies",
        "deductible_family", "deductible_family_met", "oop_max_family", "oop_max_family_met",
        "deductible_individual", "deductible_individual_met", "oop_max_individual", "oop_max_individual_met",
        "reference_number",
    }

    if expected_data:
        for fc in function_calls:
            fn_name = fc["function"]
            args = fc.get("args", {})

            # Check record_* functions
            if fn_name in func_to_field:
                state_field, arg_name = func_to_field[fn_name]
                if state_field in expected_data:
                    expected_val = expected_data[state_field]
                    actual_val = args.get(arg_name)
                    if _values_match(expected_val, actual_val):
                        checks.append(f"{fn_name}: {actual_val}")
                    else:
                        failures.append(f"{fn_name}: expected {expected_val}, got {actual_val}")

            # Check proceed_to_* functions for volunteered data (skip None values - not yet gathered)
            elif fn_name.startswith("proceed_to_"):
                for field in transition_fields:
                    if field in args and field in expected_data:
                        actual_val = args[field]
                        if actual_val is None or actual_val == "None" or actual_val == "":
                            continue  # skip fields not yet gathered
                        expected_val = expected_data[field]
                        if _values_match(expected_val, actual_val):
                            checks.append(f"{fn_name}.{field}: {actual_val}")
                        else:
                            failures.append(f"{fn_name}.{field}: expected {expected_val}, got {actual_val}")

    if failures:
        return {
            "pass": False,
            "reason": f"FAIL: {'; '.join(failures[:3])}" + (f" (+{len(failures)-3} more)" if len(failures) > 3 else ""),
        }

    if not expected_functions and not forbidden_functions and not expected_data:
        if not function_calls:
            return {"pass": False, "reason": "FAIL: No functions called"}
        return {"pass": True, "reason": f"PASS: {len(function_calls)} functions called"}

    return {"pass": True, "reason": f"PASS: {len(checks)} function checks passed"}


def grade_scenario(
    conversation: list[dict],
    function_calls: list[dict],
    final_state: dict,
    final_node: str,
    expected_node: str,
    expected_db_state: dict = None,
    expected_data: dict = None,
    expected_functions: list[str] = None,
    forbidden_functions: list[str] = None,
    forbidden_phrases: list[str] = None,
) -> dict:
    conv_text = _format_conversation(conversation)
    final_state = final_state or {}

    data_accuracy = grade_data_accuracy(conv_text, final_state, "", expected_data or {})
    # Skip quality check for transfer scenarios (conversation doesn't end normally)
    if expected_node in ("transfer_pending", "transfer_initiated", "transfer_failed"):
        quality = {"pass": True, "reason": "PASS: Skipped for transfer scenario"}
    else:
        quality = grade_conversation_quality(conv_text)
    functions = grade_function_calls(
        function_calls, final_state, expected_data,
        expected_functions, forbidden_functions,
    )
    node_reached = grade_node_reached(final_node, expected_node)
    state_check = grade_captured_state(final_state, expected_db_state or {})
    phrases_check = grade_forbidden_phrases(conversation, forbidden_phrases or [])

    all_passed = all([
        data_accuracy["pass"],
        quality["pass"],
        functions["pass"],
        node_reached["pass"],
        state_check["pass"],
        phrases_check["pass"],
    ])

    failures = []
    if not data_accuracy["pass"]:
        failures.append(f"data_accuracy: {data_accuracy['reason']}")
    if not quality["pass"]:
        failures.append(f"quality: {quality['reason']}")
    if not functions["pass"]:
        failures.append(f"functions: {functions['reason']}")
    if not node_reached["pass"]:
        failures.append(f"node: {node_reached['reason']}")
    if not state_check["pass"]:
        failures.append(f"state: {state_check['reason']}")
    if not phrases_check["pass"]:
        failures.append(f"phrases: {phrases_check['reason']}")

    reason = "PASS: All checks passed" if all_passed else "FAIL: " + "; ".join(failures)

    return {
        "pass": all_passed,
        "reason": reason,
        "details": {
            "data_accuracy": data_accuracy,
            "quality": quality,
            "functions": functions,
            "node_reached": node_reached,
            "captured_state": state_check,
            "forbidden_phrases": phrases_check,
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


# === MOCKS ===
class MockFlowManager:
    def __init__(self):
        self.state = {}


class MockPipeline:
    transcripts = []
    transfer_in_progress = False


class MockTransport:
    def __init__(self):
        self.transfers: list[dict] = []

    async def sip_call_transfer(self, config):
        self.transfers.append(config)
        print(f"\n  [TRANSFER] → {config.get('toEndPoint')}\n")
        return None  # None = success


# === FLOW RUNNER ===
class FlowRunner:
    """Runs the eligibility verification flow with patient data from scenario."""

    def __init__(
        self,
        llm_config: dict,
        cold_transfer_config: dict,
        patient_data: dict,
        session_id: str,
        organization_id: str,
        verbose: bool = False,
        entry_node: str = "greeting",
    ):
        self.mock_flow_manager = MockFlowManager()
        self.mock_pipeline = MockPipeline()
        self.mock_transport = MockTransport()
        self.llm_config = llm_config
        self.verbose = verbose

        self.flow = EligibilityVerificationFlow(
            patient_data=patient_data,
            session_id=session_id,
            flow_manager=self.mock_flow_manager,
            main_llm=None,
            context_aggregator=None,
            transport=self.mock_transport,
            pipeline=self.mock_pipeline,
            organization_id=organization_id,
            cold_transfer_config=cold_transfer_config,
        )

        # Initialize flow state with patient data
        self.flow._init_flow_state()

        # Start with greeting node (unified for with/without IVR)
        self.current_node = self.flow.create_greeting_node()
        self.current_node_name = self.current_node.get("name", "greeting")
        self.context = EvalContextManager()
        self.context.set_node(self.current_node)
        self.function_calls = []  # Track all function calls
        self.done = False
        self.end_call_invoked = False  # Track if end_call was called

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

        if self.verbose:
            # Full context output (matches production logs format)
            for i, msg in enumerate(messages):
                print(f"  [{i}] {json.dumps(msg, indent=2)}")
        else:
            # Truncated output for normal runs
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
        last_func_call = None  # Track last (func_name, args_json) to prevent loops
        respond_immediately_count = 0  # Allow up to 3 respond_immediately continuations per turn
        MAX_RESPOND_IMMEDIATELY = 3

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

                # Deduplication: prevent infinite loops from repeated identical calls
                current_func_call = (func_name, tool_call.function.arguments)
                if current_func_call == last_func_call:
                    print(f"    ⚠ Loop detected: {func_name} called with same args twice in a row, breaking")
                    break
                last_func_call = current_func_call

                # Track function call
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

                # Check if this function ends the conversation (even without a next_node)
                if func_name in ("end_call", "end_conversation", "hangup"):
                    self.done = True
                    self.end_call_invoked = True
                    break

                if next_node:
                    self.current_node = next_node
                    self.current_node_name = next_node.get("name", "unknown")
                    self.context.set_node(next_node)
                    # Process pre_actions on the new node (e.g., tts_say, function)
                    pre_actions = self.current_node.get("pre_actions") or []
                    for action in pre_actions:
                        action_type = action.get("type") if isinstance(action, dict) else getattr(action, "type", None)

                        if action_type == "tts_say":
                            text = action.get("text", "") if isinstance(action, dict) else getattr(action, "text", "")
                            if text:
                                all_content.append(text)
                                self.context.add_assistant_message(text)

                        elif action_type == "function":
                            handler = action.get("handler") if isinstance(action, dict) else getattr(action, "handler", None)
                            if handler:
                                try:
                                    await handler(action, self.mock_flow_manager)
                                    print(f"    [PRE_ACTION] Executed: {handler.__name__}")
                                except Exception as e:
                                    print(f"    [PRE_ACTION] Error: {e}")

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
                    if self.current_node.get("respond_immediately") and respond_immediately_count < MAX_RESPOND_IMMEDIATELY:
                        respond_immediately_count += 1
                        continue

            if msg.content:
                all_content.append(msg.content)
                self.context.add_assistant_message(msg.content)

            break

        return " ".join(all_content)


@observe(as_type="generation", name="insurance_simulator")
async def get_insurance_response(history: list[dict], insurance_rep: dict, persona: str) -> str:
    system_prompt = f"""You are an insurance representative.

{persona}

Your details (use these when asked):
- Name: {insurance_rep['first_name']} {insurance_rep['last_name']}
- Company: {insurance_rep.get('company', 'United Healthcare')}

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


@observe(name="eligibility_verification_eval")
async def run_simulation(
    scenario: dict,
    llm_config: dict,
    cold_transfer_config: dict,
    seeded_patient: dict,
    session_id: str,
    verbose: bool = False,
) -> dict:
    """Run a single eligibility verification simulation for a scenario."""
    insurance_rep = scenario["insurance_rep"]
    persona = scenario["persona"]
    runner = FlowRunner(
        llm_config=llm_config,
        cold_transfer_config=cold_transfer_config,
        patient_data=seeded_patient,
        session_id=session_id,
        organization_id=ORG_ID_STR,
        verbose=verbose,
    )

    # Check for pre_actions (e.g., tts_say greeting)
    pre_actions = runner.current_node.get("pre_actions") or []
    greeting = ""
    if pre_actions and pre_actions[0].get("type") == "tts_say":
        greeting = pre_actions[0].get("text", "")

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario['id']}")
    print(f"TARGET: {scenario['target_node']}")
    print(f"{'='*70}\n")

    # Handle greeting if present in pre_actions
    if greeting:
        runner.context.add_assistant_message(greeting)
        print(f"MONICA: {greeting}\n")
        conversation = [{"role": "assistant", "content": greeting, "turn": 0}]
    else:
        # Bot waits for rep to speak first (respond_immediately=False)
        conversation = []

    # Conversation loop
    turn = 0
    while not runner.done and turn < 20:
        turn += 1

        # Insurance rep responds
        insurance_msg = await get_insurance_response(
            [{"role": c["role"], "content": c["content"]} for c in conversation],
            insurance_rep,
            persona
        )
        print(f"INSURANCE: {insurance_msg}\n")
        conversation.append({"role": "user", "content": insurance_msg, "turn": turn})

        # Monica responds - keep processing until she speaks or finishes
        # This prevents the goodbye loop when bot is calling functions without speaking
        inner_iterations = 0
        MAX_INNER = 5
        while not runner.done and inner_iterations < MAX_INNER:
            inner_iterations += 1
            bot_response = await runner.process_message(insurance_msg, turn)
            if bot_response:
                print(f"MONICA: {bot_response}\n")
                conversation.append({"role": "assistant", "content": bot_response, "turn": turn})
                break
            # Bot called functions but didn't speak - let her keep processing
            # without advancing the insurance turn
        if inner_iterations >= MAX_INNER and not runner.done:
            print("    [WARN] Inner loop max iterations reached without bot speaking")

    final_state = runner.mock_flow_manager.state
    final_node = runner.current_node_name

    print(f"\n{'='*70}")
    print(f"FINAL NODE: {final_node}")
    print("FINAL STATE:")
    print(json.dumps(final_state, indent=2))
    print(f"{'='*70}\n")

    return {
        "scenario_id": scenario["id"],
        "target_node": scenario["target_node"],
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
        f.write(f"TARGET NODE: {result['target_node']}\n")
        f.write(f"FINAL NODE: {result.get('final_node', 'unknown')}\n")
        f.write(f"{'='*60}\n\n")
        for msg in result["conversation"]:
            role = "MONICA" if msg["role"] == "assistant" else "INSURANCE"
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
        },

        # Output (from run)
        "output": {
            "conversation": result["conversation"],
            "function_calls": result["function_calls"],
            "final_state": result["final_state"],
            "final_node": result.get("final_node"),
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
                "insurance_rep": scenario["insurance_rep"],
                "persona": scenario["persona"],
            },
            expected_output={
                "target_node": scenario["target_node"],
                "expected_db_state": scenario.get("expected_db_state", {}),
                "expected_data": scenario.get("expected_data", {}),
            },
            metadata={
                "target_node": scenario["target_node"],
            },
        )
        print(f"  Synced: {scenario['id']}")

    langfuse.flush()
    print(f"\nDataset synced to Langfuse: {dataset_name}")
    print("View at: https://cloud.langfuse.com/datasets")


async def run_scenario(scenario_id: str, verbose: bool = False) -> dict:
    """Run a single scenario and save results."""
    scenario = get_scenario(scenario_id)

    # Load config
    services_path = Path(__file__).parent.parent.parent.parent / "clients/demo_clinic_alpha/eligibility_verification/services.yaml"
    with open(services_path) as f:
        services = yaml.safe_load(f)

    llm_config = services["services"]["llm"]
    cold_transfer_config = services.get("cold_transfer", {})

    session_id = f"eval-{scenario_id}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Seed patient into test database
    test_db = TestDB()
    patient_id = await test_db.seed_patient(scenario, "eligibility_verification")
    seeded_patient = await test_db.get_full_patient(patient_id)
    print(f"  [DB] Seeded patient: {seeded_patient.get('patient_name', seeded_patient.get('first_name', ''))} (id: {patient_id})")

    await test_db.create_session(session_id, workflow="eligibility_verification")
    print(f"  [SESSION] Created session: {session_id}")

    try:
        # Run simulation (trace is created by @observe decorator)
        result = await run_simulation(
            scenario, llm_config, cold_transfer_config,
            seeded_patient, session_id, verbose=verbose
        )

        grade = grade_scenario(
            conversation=result["conversation"],
            function_calls=result["function_calls"],
            final_state=result["final_state"],
            final_node=result["final_node"],
            expected_node=result["target_node"],
            expected_db_state=scenario.get("expected_db_state"),
            expected_data=scenario.get("expected_data"),
            expected_functions=scenario.get("expected_functions"),
            forbidden_functions=scenario.get("forbidden_functions"),
            forbidden_phrases=scenario.get("forbidden_phrases"),
        )

        # Verify DB state after call
        db_captured = await test_db.get_captured_fields(patient_id)
        result["db_captured_fields"] = db_captured

        # Compare DB state against expected_db_state if defined
        if scenario.get("expected_db_state"):
            db_grade = grade_captured_state(db_captured, scenario["expected_db_state"])
            if not db_grade["pass"]:
                grade["pass"] = False
                grade["reason"] += f"; DB: {db_grade['reason']}"
                grade["details"]["db_state"] = db_grade
            else:
                grade["details"]["db_state"] = db_grade

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
        print("  [CLEANUP] Removed test patient and session")


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
    parser = argparse.ArgumentParser(description="Eligibility Verification Flow Evaluation")
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
    first_scenario = config["scenarios"][0]["id"]
    print(f"No scenario specified, running default: {first_scenario}")
    print("Use --list to see all scenarios, --scenario <id> to run specific one\n")
    await run_scenario(first_scenario, verbose=args.verbose)


if __name__ == "__main__":
    asyncio.run(main())
