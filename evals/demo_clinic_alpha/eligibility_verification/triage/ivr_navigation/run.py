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
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

from langfuse import Langfuse, observe
from openai import AsyncOpenAI

from clients.demo_clinic_alpha.eligibility_verification.flow_definition import (
    EligibilityVerificationFlow,
)
from evals.triage import (
    get_scenario,
    grade_dtmf_sequence,
    grade_single_dtmf,
    grade_spoken_text,
    grade_status,
    load_scenarios,
)
from pipeline.ivr_navigation_processor import (
    DTMF_PATTERN,
    IVR_STATUS_PATTERN,
    IVREvent,
    IVRNavigationProcessor,
    IVRStatus,
)
from pipeline.pipeline_factory import PipelineFactory

# === LANGFUSE CLIENT ===
langfuse = Langfuse()


# === CONSTANTS ===
SCENARIOS_PATH = Path(__file__).parent / "scenarios.yaml"

# Test patient data for IVR navigation evals
TEST_PATIENT_DATA = {
    "provider_agent_first_name": "Maria Chen",
    "facility_name": "Westbrook Family Medicine",
    "tax_id": "84-7291035",
    "provider_name": "Dr. Sarah Okonkwo",
    "provider_npi": "1928374650",
    "provider_call_back_phone": "555-847-2910",
    "insurance_member_id": "WDH492817365",
    "patient_name": "Robert Martinez",
    "date_of_birth": "07/14/1982",
}

# Format navigation goal with test data
NAVIGATION_GOAL = EligibilityVerificationFlow.IVR_NAVIGATION_GOAL.format(**TEST_PATIENT_DATA)

# Load production LLM config from services.yaml (same pattern as classification eval)
_services_config = PipelineFactory.load_services_config("demo_clinic_alpha", "eligibility_verification")
NAVIGATION_LLM_CONFIG = _services_config["services"]["llm"]


def grade_dtmf_selection(
    menu_prompt: str,
    expected_action: str,
    actual_response: str,
    reasoning: str
) -> dict:
    dtmf_match = re.search(DTMF_PATTERN, actual_response)
    actual_dtmf = dtmf_match.group(1) if dtmf_match else None
    actual_dtmf_seq = "".join(re.findall(DTMF_PATTERN, actual_response))
    ivr_match = re.search(IVR_STATUS_PATTERN, actual_response, re.IGNORECASE)
    actual_status = ivr_match.group(1).lower() if ivr_match else None
    clean_response = re.sub(r'<[^>]+>', '', actual_response).strip()

    if expected_action.startswith("dtmf:"):
        return grade_single_dtmf(expected_action.split(":")[1], actual_dtmf)
    if expected_action.startswith("dtmf_sequence:"):
        return grade_dtmf_sequence(expected_action.split(":")[1], actual_dtmf_seq)
    if expected_action.startswith("speak:"):
        return grade_spoken_text(expected_action.split(":", 1)[1], clean_response)
    if expected_action.startswith("status:"):
        return grade_status(expected_action.split(":")[1], actual_status)
    return {"pass": False, "reason": f"FAIL: Unknown action format: {expected_action}"}


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


# === SCENARIO LOADING (custom list for IVR-specific format) ===
def list_scenarios_formatted() -> None:
    """Print available scenarios with IVR-specific format."""
    config = load_scenarios(SCENARIOS_PATH)
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


def get_expected_event(action: str) -> tuple[str | None, str | None]:
    """Map expected action to event name and expected arg using production constants.

    Returns:
        Tuple of (event_name, expected_arg) or (None, None) for wait
    """
    if action.startswith("dtmf:"):
        return IVREvent.DTMF_PRESSED, action.split(":")[1]
    elif action.startswith("status:"):
        status = action.split(":")[1]
        if status in (IVRStatus.COMPLETED, IVRStatus.STUCK):
            return IVREvent.STATUS_CHANGED, status
        # "wait" doesn't fire an event
        return None, None
    return None, None


@observe(name="ivr_navigation_eval")
async def run_simulation(scenario: dict, navigation_goal: str, llm_config: dict) -> dict:
    """Run navigation for a complete scenario (multi-step).

    1. Call OpenAI LLM to get navigation response
    2. Pass result through production IVRNavigationProcessor._aggregator.aggregate()
    3. Verify correct events fire via EventCollector
    """

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

        # Step 1: Get LLM navigation response
        actual_response = await runner.navigate_step(menu_prompt)
        print(f"  Actual: {actual_response}")

        # Step 2: Parse LLM response using production patterns
        dtmf_match = re.search(DTMF_PATTERN, actual_response)
        ivr_match = re.search(IVR_STATUS_PATTERN, actual_response, re.IGNORECASE)

        actual_dtmf = dtmf_match.group(1) if dtmf_match else None
        actual_status = ivr_match.group(1).lower() if ivr_match else None

        # Determine what event would fire using production constants
        expected_event, expected_arg = get_expected_event(expected_action)

        if actual_dtmf:
            actual_event = IVREvent.DTMF_PRESSED
            actual_arg = actual_dtmf
        elif actual_status in (IVRStatus.COMPLETED, IVRStatus.STUCK):
            actual_event = IVREvent.STATUS_CHANGED
            actual_arg = actual_status
        else:
            actual_event = None
            actual_arg = None

        event_matched = (actual_event == expected_event) if expected_event else (actual_event is None)
        arg_matched = (actual_arg == expected_arg) if expected_arg else True

        print(f"  Expected event: {expected_event}({expected_arg})")
        print(f"  Actual event: {actual_event}({actual_arg})")
        print(f"  Event matched: {'YES' if event_matched and arg_matched else 'NO'}\n")

        step_results.append({
            "step_number": i + 1,
            "menu_prompt": menu_prompt,
            "expected_action": expected_action,
            "actual_response": actual_response,
            "reasoning": reasoning,
            "expected_event": expected_event,
            "actual_event": actual_event,
            "expected_arg": expected_arg,
            "actual_arg": actual_arg,
            "event_matched": event_matched and arg_matched,
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
    config = load_scenarios(SCENARIOS_PATH)
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
    config = load_scenarios(SCENARIOS_PATH)
    scenario = get_scenario(SCENARIOS_PATH, scenario_id)

    # Use goal from EligibilityVerificationFlow, allow override in scenarios.yaml
    navigation_goal = config.get("navigation_goal", NAVIGATION_GOAL)
    # LLM config inherited from production services.yaml
    llm_config = NAVIGATION_LLM_CONFIG

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
    config = load_scenarios(SCENARIOS_PATH)
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
        list_scenarios_formatted()
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
    config = load_scenarios(SCENARIOS_PATH)
    first_scenario = config["scenarios"][0]["id"]
    print(f"No scenario specified, running default: {first_scenario}")
    print("Use --list to see all scenarios, --scenario <id> to run specific one\n")
    await run_scenario(first_scenario)


if __name__ == "__main__":
    asyncio.run(main())
