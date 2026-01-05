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
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langfuse import Langfuse, observe
from pipecat.processors.frame_processor import FrameProcessor

from clients.demo_clinic_alpha.eligibility_verification.flow_definition import EligibilityVerificationFlow
from pipeline.triage_detector import TriageDetector
from pipeline.triage_processors import CLASSIFICATION_TO_EVENT, TriageEvent
from evals.triage import EventCollector, call_grader, load_scenarios, get_scenario


# === LANGFUSE CLIENT ===
langfuse = Langfuse()


# === CONSTANTS ===
SCENARIOS_PATH = Path(__file__).parent / "scenarios.yaml"
CLASSIFIER_PROMPT = EligibilityVerificationFlow.TRIAGE_CLASSIFIER_PROMPT


# === MOCK LLM FOR TRIAGE DETECTOR ===
class MockLLMService(FrameProcessor):
    """Minimal mock LLM - we call _process_classification directly."""
    pass


# === LLM GRADERS ===
def grade_classification(utterance: str, expected: str, actual: str, reasoning: str) -> dict:
    """Grade classification correctness with LLM for nuanced cases."""

    # Exact match is always a pass
    if actual.upper() == expected.upper():
        return {"pass": True, "reason": f"PASS: Correct classification ({actual})"}

    # For mismatches, use LLM to check if it's actually reasonable
    prompt = f"""Grade this call classification decision.

AUDIO TRANSCRIPTION:
"{utterance}"

EXPECTED CLASSIFICATION: {expected}
ACTUAL CLASSIFICATION: {actual}

CONTEXT:
{reasoning}

Is the actual classification REASONABLE given the transcription?
- CONVERSATION: Human speaking naturally, greetings, questions
- IVR: Automated menus with "Press X for...", system prompts
- VOICEMAIL: "Leave a message", after-hours, carrier messages

If the actual classification is defensible given the utterance, mark as PASS.
If clearly wrong, mark as FAIL.

Reply with exactly one line:
PASS: <brief reason>
or
FAIL: <brief reason>"""

    result = call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_confidence(utterance: str, classification: str, is_edge_case: bool) -> dict:
    """Grade whether the model showed appropriate confidence for the input type."""

    if not is_edge_case:
        return {"pass": True, "reason": "PASS: Clear case handled correctly"}

    prompt = f"""Grade the classification confidence for this edge case.

AUDIO TRANSCRIPTION:
"{utterance}"

CLASSIFICATION: {classification}

This is marked as an EDGE CASE - ambiguous input that could go multiple ways.

Did the classifier handle this reasonably? Edge cases should either:
1. Pick the most likely classification
2. Not hallucinate features not present in the input

Reply with exactly one line:
PASS: <brief reason>
or
FAIL: <brief reason>"""

    result = call_grader(prompt)
    return {"pass": result.upper().startswith("PASS"), "reason": result}


def grade_scenario(utterance: str, expected: str, actual: str, reasoning: str, is_edge_case: bool) -> dict:
    """
    Run all graders and combine results. ALL must pass for overall pass.
    Returns: {"pass": bool, "reason": str, "details": {...}}
    """
    classification_grade = grade_classification(utterance, expected, actual, reasoning)
    confidence_grade = grade_confidence(utterance, actual, is_edge_case)

    all_passed = classification_grade["pass"] and confidence_grade["pass"]

    failures = []
    if not classification_grade["pass"]:
        failures.append(f"classification: {classification_grade['reason']}")
    if not confidence_grade["pass"]:
        failures.append(f"confidence: {confidence_grade['reason']}")

    if all_passed:
        reason = "PASS: All checks passed"
    else:
        reason = "FAIL: " + "; ".join(failures)

    return {
        "pass": all_passed,
        "reason": reason,
        "details": {
            "classification": classification_grade,
            "confidence": confidence_grade,
        }
    }


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
    """Runs classification using Groq (same as production)."""

    def __init__(self, classifier_prompt: str, llm_config: dict):
        self.classifier_prompt = classifier_prompt
        self.llm_config = llm_config

    @observe(as_type="generation", name="classifier_llm")
    async def classify(self, utterance: str) -> str:
        """Classify a single utterance."""
        import groq

        messages = [
            {"role": "system", "content": self.classifier_prompt},
            {"role": "user", "content": utterance}
        ]

        client = groq.Groq(api_key=os.getenv("GROQ_API_KEY"))

        response = client.chat.completions.create(
            model=self.llm_config.get("model", "llama-3.3-70b-versatile"),
            messages=messages,
            temperature=0,
            max_tokens=50,
        )

        return response.choices[0].message.content.strip()


def get_expected_event(classification: str) -> str | None:
    """Map classification to expected event name using production constants."""
    return CLASSIFICATION_TO_EVENT.get(classification.upper())


@observe(name="classification_eval")
async def run_simulation(scenario: dict, classifier_prompt: str, llm_config: dict) -> dict:
    """Run classification for a single scenario.

    1. Call Groq LLM to get classification text
    2. Pass result through production TriageProcessor._process_classification()
    3. Verify correct event fires via EventCollector
    """
    utterance = scenario["utterance"]
    expected = scenario["expected_classification"]

    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario['id']}")
    print(f"EXPECTED: {expected}")
    print(f"{'='*70}\n")

    print(f"UTTERANCE: {utterance}\n")

    # Step 1: Get LLM classification (same as before)
    runner = ClassifierRunner(classifier_prompt, llm_config)
    actual = await runner.classify(utterance)

    print(f"CLASSIFICATION: {actual}\n")

    # Step 2: Verify production TriageProcessor fires correct event
    collector = EventCollector()
    mock_llm = MockLLMService()

    triage = TriageDetector(
        classifier_llm=mock_llm,
        classifier_prompt=classifier_prompt,
        voicemail_response_delay=0.1,  # Short delay for tests
    )

    # Register event handlers using production constants
    triage.add_event_handler(TriageEvent.CONVERSATION_DETECTED, collector.handler(TriageEvent.CONVERSATION_DETECTED))
    triage.add_event_handler(TriageEvent.IVR_DETECTED, collector.handler(TriageEvent.IVR_DETECTED))
    triage.add_event_handler(TriageEvent.VOICEMAIL_DETECTED, collector.handler(TriageEvent.VOICEMAIL_DETECTED))

    # Pass LLM result through production processor
    await triage._triage_processor._process_classification(actual)
    await asyncio.sleep(0.15)  # Allow voicemail delay to complete

    # Check which event fired
    expected_event = get_expected_event(expected)
    actual_event = collector.events[0][0] if collector.events else None
    event_matched = actual_event == expected_event

    print(f"EXPECTED EVENT: {expected_event}")
    print(f"ACTUAL EVENT: {actual_event}")
    print(f"EVENT MATCHED: {'YES' if event_matched else 'NO'}\n")

    return {
        "scenario_id": scenario["id"],
        "utterance": utterance,
        "expected_classification": expected,
        "actual_classification": actual,
        "expected_event": expected_event,
        "actual_event": actual_event,
        "event_matched": event_matched,
        "reasoning": scenario.get("reasoning", ""),
        "is_edge_case": scenario.get("is_edge_case", False),
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

    # Use prompt from EligibilityVerificationFlow, allow override in scenarios.yaml
    classifier_prompt = config.get("classifier_prompt", CLASSIFIER_PROMPT)
    llm_config = config.get("classifier_llm", {"model": "llama-3.3-70b-versatile"})

    result = await run_simulation(scenario, classifier_prompt, llm_config)

    grade = grade_scenario(
        result["utterance"],
        result["expected_classification"],
        result["actual_classification"],
        result["reasoning"],
        result["is_edge_case"]
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
    print(f"Use --list to see all scenarios, --scenario <id> to run specific one\n")
    await run_scenario(first_scenario)


if __name__ == "__main__":
    asyncio.run(main())
