"""
Eligibility Verification Triage Classification Evaluation Runner

Tests 3-way call classification (CONVERSATION/IVR/VOICEMAIL) using the EligibilityVerificationFlow
triage configuration. Scenarios test insurance company greeting patterns.

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
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

from langfuse import Langfuse, observe

from clients.demo_clinic_alpha.eligibility_verification.flow_definition import (
    EligibilityVerificationFlow,
)
from evals.triage import get_scenario, load_scenarios
from pipeline.pipeline_factory import PipelineFactory

# === LANGFUSE CLIENT ===
langfuse = Langfuse()


# === CONSTANTS ===
SCENARIOS_PATH = Path(__file__).parent / "scenarios.yaml"
CLASSIFIER_PROMPT = EligibilityVerificationFlow.TRIAGE_CLASSIFIER_PROMPT

# Load production LLM config from services.yaml
_services_config = PipelineFactory.load_services_config("demo_clinic_alpha", "eligibility_verification")
CLASSIFIER_LLM_CONFIG = _services_config["services"]["classifier_llm"]


# === GRADING ===
def grade_scenario(expected: str, actual: str) -> dict:
    """Binary comparison: did actual match expected?"""
    passed = actual.upper() == expected.upper()
    if passed:
        return {"pass": True, "reason": f"Correct: {actual}"}
    else:
        return {"pass": False, "reason": f"Expected {expected}, got {actual}"}


# === SCENARIO LOADING (custom list_scenarios for classification-specific format) ===
def list_scenarios_formatted() -> None:
    """Print available scenarios with classification-specific format."""
    config = load_scenarios(SCENARIOS_PATH)
    print("\nAvailable scenarios:\n")
    for s in config["scenarios"]:
        edge = " [EDGE]" if s.get("is_edge_case") else ""
        print(f"  {s['id']:<30} [{s['expected_classification']}]{edge}")
        print(f"    {s['utterance'][:60]}...\n" if len(s['utterance']) > 60 else f"    {s['utterance']}\n")


# === CLASSIFIER RUNNER ===
class ClassifierRunner:
    """Runs classification using production LLM config from services.yaml."""

    def __init__(self, classifier_prompt: str):
        self.classifier_prompt = classifier_prompt
        self.llm_config = CLASSIFIER_LLM_CONFIG

    @observe(as_type="generation", name="classifier_llm")
    async def classify(self, utterance: str) -> str:
        """Classify a single utterance using production config."""
        import groq

        messages = [
            {"role": "system", "content": self.classifier_prompt},
            {"role": "user", "content": utterance}
        ]

        client = groq.Groq(api_key=self.llm_config["api_key"])

        response = client.chat.completions.create(
            model=self.llm_config["model"],
            messages=messages,
            temperature=self.llm_config.get("temperature", 0),
            max_tokens=self.llm_config.get("max_tokens", 128),
        )

        return response.choices[0].message.content.strip()


@observe(name="classification_eval")
async def run_simulation(scenario: dict, classifier_prompt: str) -> dict:
    """Run classification for a single scenario.

    Tests the production classifier prompt + LLM to verify correct classification.
    """
    utterance = scenario["utterance"]
    expected = scenario["expected_classification"]

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario['id']}")
    print(f"EXPECTED: {expected}")
    print(f"{'='*70}\n")

    print(f"UTTERANCE: {utterance}\n")

    # Get LLM classification using production config
    runner = ClassifierRunner(classifier_prompt)
    actual = await runner.classify(utterance)

    print(f"CLASSIFICATION: {actual}\n")

    return {
        "scenario_id": scenario["id"],
        "utterance": utterance,
        "expected_classification": expected,
        "actual_classification": actual,
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
        f.write(f"EXPECTED: {result['expected_classification']}\n")
        f.write(f"ACTUAL: {result['actual_classification']}\n")
        f.write(f"{'='*60}\n\n")
        f.write(f"UTTERANCE:\n{result['utterance']}\n\n")
        f.write(f"{'='*60}\n")
        f.write(f"GRADE: {'PASS' if grade['pass'] else 'FAIL'} - {grade['reason']}\n")

    output = {
        "id": f"{result['scenario_id']}_{timestamp}",
        "scenario_id": result["scenario_id"],
        "timestamp": datetime.now().isoformat(),
        "langfuse_trace_id": trace_id,
        "langfuse_trace_url": f"https://cloud.langfuse.com/trace/{trace_id}",
        "input": {
            "utterance": result["utterance"],
            "expected_classification": result["expected_classification"],
        },
        "output": {
            "actual_classification": result["actual_classification"],
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
                "utterance": scenario["utterance"],
            },
            expected_output={
                "classification": scenario["expected_classification"],
            },
            metadata={
                "is_edge_case": scenario.get("is_edge_case", False),
                "reasoning": scenario.get("reasoning", ""),
            },
        )
        print(f"  Synced: {scenario['id']}")

    langfuse.flush()
    print(f"\nDataset synced to Langfuse: {dataset_name}")


async def run_scenario(scenario_id: str) -> dict:
    """Run a single scenario and save results."""
    config = load_scenarios(SCENARIOS_PATH)
    scenario = get_scenario(SCENARIOS_PATH, scenario_id)

    # Use production prompt from EligibilityVerificationFlow
    classifier_prompt = config.get("classifier_prompt", CLASSIFIER_PROMPT)

    result = await run_simulation(scenario, classifier_prompt)

    grade = grade_scenario(
        result["expected_classification"],
        result["actual_classification"],
    )

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
    parser = argparse.ArgumentParser(description="Prior Auth Triage Classification Evaluation")
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
