"""
Eligibility Verification IVR Navigation Evaluation Runner

Tests DTMF menu navigation using IVRNavigationProcessor logic with EligibilityVerificationFlow's
navigation goal. Scenarios test insurance company IVR menus (BCBS, Aetna, Cigna, etc.).

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
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

import yaml
from anthropic import Anthropic
from openai import AsyncOpenAI
from langfuse import Langfuse, observe

from pipeline.ivr_navigation_processor import IVRNavigationProcessor
from clients.demo_clinic_alpha.eligibility_verification.flow_definition import EligibilityVerificationFlow


# === LANGFUSE CLIENT ===
langfuse = Langfuse()


# === NAVIGATION GOAL FROM FLOW ===
NAVIGATION_GOAL = EligibilityVerificationFlow.IVR_NAVIGATION_GOAL


# === LLM GRADERS ===
def _call_grader(prompt: str) -> str:
    """Call the grader LLM with a prompt."""
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def grade_dtmf_selection(
    menu_prompt: str,
    expected_action: str,
    actual_response: str,
    reasoning: str
) -> dict:
    """Grade whether the correct DTMF was selected."""

    # Extract DTMF from response
    dtmf_match = re.search(r'<dtmf>(\d|\*|#)</dtmf>', actual_response)
    actual_dtmf = dtmf_match.group(1) if dtmf_match else None

    # Extract IVR status from response
    ivr_match = re.search(r'<ivr>(completed|stuck|wait)</ivr>', actual_response, re.IGNORECASE)
    actual_status = ivr_match.group(1).lower() if ivr_match else None

    # Check expected action
    if expected_action.startswith("dtmf:"):
        expected_dtmf = expected_action.split(":")[1]
        if actual_dtmf == expected_dtmf:
            return {"pass": True, "reason": f"PASS: Correct DTMF ({actual_dtmf})"}

        # Use LLM for nuanced grading
        prompt = f"""Grade this IVR navigation decision.

MENU PROMPT:
"{menu_prompt}"

EXPECTED ACTION: Press {expected_dtmf}
ACTUAL RESPONSE: {actual_response}

CONTEXT: {reasoning}

Is the actual response REASONABLE for navigating to provider services/eligibility?
- If pressed a different valid option that could reach the goal, PASS
- If pressed wrong option that leads away from goal, FAIL
- If no DTMF found when one was needed, FAIL

Reply with exactly one line:
PASS: <brief reason>
or
FAIL: <brief reason>"""

        result = _call_grader(prompt)
        return {"pass": result.upper().startswith("PASS"), "reason": result}

    elif expected_action.startswith("status:"):
        expected_status = expected_action.split(":")[1]
        if actual_status == expected_status:
            return {"pass": True, "reason": f"PASS: Correct status ({actual_status})"}

        prompt = f"""Grade this IVR navigation decision.

PROMPT:
"{menu_prompt}"

EXPECTED: <ivr>{expected_status}</ivr>
ACTUAL RESPONSE: {actual_response}

CONTEXT: {reasoning}

Is the actual response REASONABLE?
- "completed" when transfer is happening or goal reached
- "stuck" when no relevant options or repeated loops
- "wait" when need more information

Reply with exactly one line:
PASS: <brief reason>
or
FAIL: <brief reason>"""

        result = _call_grader(prompt)
        return {"pass": result.upper().startswith("PASS"), "reason": result}

    return {"pass": False, "reason": f"FAIL: Unknown expected action format: {expected_action}"}


def grade_step(step_result: dict) -> dict:
    """Grade a single navigation step."""
    return grade_dtmf_selection(
        step_result["menu_prompt"],
        step_result["expected_action"],
        step_result["actual_response"],
        step_result["reasoning"]
    )


def grade_scenario(steps: list[dict]) -> dict:
    """
    Grade all steps in a scenario. ALL must pass for overall pass.
    Returns: {"pass": bool, "reason": str, "step_grades": [...]}
    """
    step_grades = []
    for i, step in enumerate(steps):
        grade = grade_step(step)
        step_grades.append({
            "step": i + 1,
            "menu_prompt": step["menu_prompt"][:50] + "...",
            **grade
        })

    all_passed = all(g["pass"] for g in step_grades)
    failed_steps = [g for g in step_grades if not g["pass"]]

    if all_passed:
        reason = f"PASS: All {len(step_grades)} steps correct"
    else:
        reason = f"FAIL: {len(failed_steps)}/{len(step_grades)} steps failed"

    return {
        "pass": all_passed,
        "reason": reason,
        "step_grades": step_grades,
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
        print(f"  {s['id']:<35} [{len(s['steps'])} steps]")
        print(f"    {s['description']}\n")


# === NAVIGATION RUNNER ===
class NavigationRunner:
    """Runs IVR navigation using OpenAI (same as production)."""

    def __init__(self, navigation_goal: str, llm_config: dict):
        self.navigation_prompt = IVRNavigationProcessor.IVR_NAVIGATION_PROMPT.format(
            goal=navigation_goal
        )
        self.llm_config = llm_config
        self.conversation_history = []

    @observe(as_type="generation", name="navigation_llm")
    async def navigate_step(self, menu_prompt: str) -> str:
        """Process a single IVR menu prompt and return navigation response."""

        # Add menu prompt to history
        self.conversation_history.append({"role": "user", "content": menu_prompt})

        # Build messages
        messages = [
            {"role": "system", "content": self.navigation_prompt},
            *self.conversation_history
        ]

        # Call OpenAI (same as production)
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model=self.llm_config.get("model", "gpt-4o"),
            messages=messages,
            temperature=0,
            max_tokens=100,
        )

        result = response.choices[0].message.content.strip()

        # Add response to history for multi-step conversations
        self.conversation_history.append({"role": "assistant", "content": result})

        return result


@observe(name="ivr_navigation_eval")
async def run_simulation(scenario: dict, navigation_goal: str, llm_config: dict) -> dict:
    """Run navigation for a complete scenario (multi-step)."""

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario['id']}")
    print(f"DESCRIPTION: {scenario['description']}")
    print(f"{'='*70}\n")

    runner = NavigationRunner(navigation_goal, llm_config)
    step_results = []

    for i, step in enumerate(scenario["steps"]):
        menu_prompt = step["menu_prompt"]
        expected_action = step["expected_action"]
        reasoning = step["reasoning"]

        print(f"STEP {i+1}: {menu_prompt[:80]}...")
        print(f"  Expected: {expected_action}")

        actual_response = await runner.navigate_step(menu_prompt)

        print(f"  Actual: {actual_response}\n")

        step_results.append({
            "step_number": i + 1,
            "menu_prompt": menu_prompt,
            "expected_action": expected_action,
            "actual_response": actual_response,
            "reasoning": reasoning,
        })

    return {
        "scenario_id": scenario["id"],
        "description": scenario["description"],
        "steps": step_results,
        "conversation_history": runner.conversation_history,
    }


def save_result(result: dict, trace_id: str, grade: dict) -> Path:
    """Save result to local files."""
    results_dir = Path(__file__).parent / "results" / result["scenario_id"]
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    result_file = results_dir / f"{timestamp}.json"

    # Save human-readable transcript
    txt_file = results_dir / f"{timestamp}.txt"
    with open(txt_file, "w") as f:
        f.write(f"SCENARIO: {result['scenario_id']}\n")
        f.write(f"DESCRIPTION: {result['description']}\n")
        f.write(f"{'='*60}\n\n")

        for step in result["steps"]:
            f.write(f"STEP {step['step_number']}:\n")
            f.write(f"  Menu: {step['menu_prompt']}\n")
            f.write(f"  Expected: {step['expected_action']}\n")
            f.write(f"  Actual: {step['actual_response']}\n\n")

        f.write(f"{'='*60}\n")
        f.write(f"GRADE: {'PASS' if grade['pass'] else 'FAIL'} - {grade['reason']}\n\n")

        if grade.get("step_grades"):
            f.write("STEP GRADES:\n")
            for sg in grade["step_grades"]:
                status = "PASS" if sg["pass"] else "FAIL"
                f.write(f"  Step {sg['step']}: {status} - {sg['reason']}\n")

    output = {
        "id": f"{result['scenario_id']}_{timestamp}",
        "scenario_id": result["scenario_id"],
        "timestamp": datetime.now().isoformat(),
        "langfuse_trace_id": trace_id,
        "langfuse_trace_url": f"https://cloud.langfuse.com/trace/{trace_id}",
        "input": {
            "description": result["description"],
            "steps": [{"menu_prompt": s["menu_prompt"], "expected_action": s["expected_action"]} for s in result["steps"]],
        },
        "output": {
            "steps": result["steps"],
            "conversation_history": result["conversation_history"],
        },
        "grade": grade,
        "notes": "",
    }

    with open(result_file, "w") as f:
        json.dump(output, f, indent=2)

    return result_file


def sync_dataset_to_langfuse() -> None:
    """Sync scenarios to Langfuse as a dataset."""
    config = load_scenarios()
    dataset_name = config["dataset_name"]

    try:
        langfuse.create_dataset(
            name=dataset_name,
            description=config.get("dataset_description", ""),
            metadata={"source": "scenarios.yaml"},
        )
        print(f"Created dataset: {dataset_name}")
    except Exception:
        print(f"Dataset '{dataset_name}' already exists, updating items...")

    for scenario in config["scenarios"]:
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            id=scenario["id"],
            input={
                "description": scenario["description"],
                "steps": scenario["steps"],
            },
            expected_output={
                "all_steps_pass": True,
            },
            metadata={
                "step_count": len(scenario["steps"]),
            },
        )
        print(f"  Synced: {scenario['id']}")

    langfuse.flush()
    print(f"\nDataset synced to Langfuse: {dataset_name}")


async def run_scenario(scenario_id: str) -> dict:
    """Run a single scenario and save results."""
    config = load_scenarios()
    scenario = get_scenario(scenario_id)

    # Use goal from EligibilityVerificationFlow, allow override in scenarios.yaml
    navigation_goal = config.get("navigation_goal", NAVIGATION_GOAL)
    llm_config = config.get("navigation_llm", {"model": "gpt-4o"})

    result = await run_simulation(scenario, navigation_goal, llm_config)

    grade = grade_scenario(result["steps"])

    status = "PASS" if grade["pass"] else "FAIL"
    print(f"\n{status} | {scenario_id}")
    print(f"  {grade['reason']}")

    trace_id = langfuse.get_current_trace_id() or langfuse.create_trace_id()
    result_file = save_result(result, trace_id, grade)
    print(f"Saved: {result_file}")

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
    parser = argparse.ArgumentParser(description="Prior Auth IVR Navigation Evaluation")
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
