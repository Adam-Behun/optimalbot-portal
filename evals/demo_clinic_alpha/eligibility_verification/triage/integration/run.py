"""
Eligibility Verification Triage Integration Evaluation Runner

Tests transitions between classification, IVR navigation, and conversation.
These test actual processor logic (events, state changes) without LLM calls.

Usage:
    python run.py                           # Run default scenario (first in list)
    python run.py --scenario <id>           # Run specific scenario
    python run.py --all                     # Run all scenarios
    python run.py --list                    # List available scenarios

Results are stored locally in results/<scenario_id>/
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

from pipecat.processors.frame_processor import FrameProcessor

from pipeline.triage_detector import TriageDetector
from pipeline.triage_processors import TriageEvent
from pipeline.ivr_navigation_processor import IVRNavigationProcessor, IVRStatus, IVREvent
from clients.demo_clinic_alpha.eligibility_verification.flow_definition import EligibilityVerificationFlow
from evals.triage import EventCollector, MockMatch, load_scenarios, get_scenario, list_scenarios


# === CONSTANTS ===
SCENARIOS_PATH = Path(__file__).parent / "scenarios.yaml"


# =============================================================================
# MOCK HELPERS
# =============================================================================

class MockLLMService(FrameProcessor):
    """Minimal mock LLM that can be linked in a pipeline.

    We call _process_classification directly, so this never processes frames.
    """
    pass


# =============================================================================
# TEST RUNNERS
# =============================================================================

async def run_classification_to_ivr(scenario: dict, flow_config: dict) -> dict:
    """Test classification → IVR transition."""
    collector = EventCollector()
    mock_llm = MockLLMService()

    triage = TriageDetector(
        classifier_llm=mock_llm,
        classifier_prompt=flow_config["classifier_prompt"],
    )

    triage.add_event_handler(TriageEvent.IVR_DETECTED, collector.handler(TriageEvent.IVR_DETECTED))

    # Add conversation history if provided
    for msg in scenario.get("conversation_history", []):
        triage._context.add_message(msg)

    # Run classification
    await triage._triage_processor._process_classification(scenario["classifier_response"])
    await asyncio.sleep(0.05)

    # Check results
    passed = True
    reason = ""

    expected_event = scenario.get("expected_event")
    if expected_event:
        if len(collector.events) == 0:
            passed = False
            reason = f"Expected {expected_event} but no events fired"
        elif collector.events[0][0] != expected_event:
            passed = False
            reason = f"Expected {expected_event} but got {collector.events[0][0]}"

    if scenario.get("expected_decision_made") is not None:
        actual = triage._triage_processor._decision_made
        if actual != scenario["expected_decision_made"]:
            passed = False
            reason = f"Expected decision_made={scenario['expected_decision_made']} but got {actual}"

    if scenario.get("expected_history_length") is not None:
        if len(collector.events) > 0:
            history = collector.events[0][1][0]  # First arg of first event
            if len(history) != scenario["expected_history_length"]:
                passed = False
                reason = f"Expected history length {scenario['expected_history_length']} but got {len(history)}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
        "decision_made": triage._triage_processor._decision_made,
    }


async def run_classification_to_conversation(scenario: dict, flow_config: dict) -> dict:
    """Test classification → conversation transition."""
    collector = EventCollector()
    mock_llm = MockLLMService()

    triage = TriageDetector(
        classifier_llm=mock_llm,
        classifier_prompt=flow_config["classifier_prompt"],
    )

    triage.add_event_handler(TriageEvent.CONVERSATION_DETECTED, collector.handler(TriageEvent.CONVERSATION_DETECTED))

    for msg in scenario.get("conversation_history", []):
        triage._context.add_message(msg)

    await triage._triage_processor._process_classification(scenario["classifier_response"])
    await asyncio.sleep(0.05)

    passed = True
    reason = ""

    expected_event = scenario.get("expected_event")
    if expected_event:
        if len(collector.events) == 0:
            passed = False
            reason = f"Expected {expected_event} but no events fired"
        elif collector.events[0][0] != expected_event:
            passed = False
            reason = f"Expected {expected_event} but got {collector.events[0][0]}"

    if scenario.get("expected_history_length") is not None and len(collector.events) > 0:
        history = collector.events[0][1][0]
        if len(history) != scenario["expected_history_length"]:
            passed = False
            reason = f"Expected history length {scenario['expected_history_length']} but got {len(history)}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
    }


async def run_classification_to_voicemail(scenario: dict, flow_config: dict) -> dict:
    """Test classification → voicemail transition."""
    mock_llm = MockLLMService()

    triage = TriageDetector(
        classifier_llm=mock_llm,
        classifier_prompt=flow_config["classifier_prompt"],
        voicemail_response_delay=0.1,
    )

    await triage._triage_processor._process_classification(scenario["classifier_response"])

    passed = True
    reason = ""

    if scenario.get("expected_voicemail_detected") is not None:
        actual = triage._triage_processor._voicemail_detected
        if actual != scenario["expected_voicemail_detected"]:
            passed = False
            reason = f"Expected voicemail_detected={scenario['expected_voicemail_detected']} but got {actual}"

    if scenario.get("expected_decision_made") is not None:
        actual = triage._triage_processor._decision_made
        if actual != scenario["expected_decision_made"]:
            passed = False
            reason = f"Expected decision_made={scenario['expected_decision_made']} but got {actual}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "voicemail_detected": triage._triage_processor._voicemail_detected,
        "decision_made": triage._triage_processor._decision_made,
    }


async def run_ivr_status(scenario: dict, flow_config: dict) -> dict:
    """Test IVR status transitions."""
    collector = EventCollector()
    ivr_processor = IVRNavigationProcessor()

    ivr_processor.add_event_handler(IVREvent.STATUS_CHANGED, collector.handler(IVREvent.STATUS_CHANGED))
    ivr_processor._active = True

    await ivr_processor._handle_ivr_action(MockMatch(scenario["ivr_status"]))
    await asyncio.sleep(0.05)

    passed = True
    reason = ""

    expected_event = scenario.get("expected_event")
    if expected_event is None:
        if len(collector.events) > 0:
            passed = False
            reason = f"Expected no events but got {len(collector.events)}"
    else:
        if len(collector.events) == 0:
            passed = False
            reason = f"Expected {expected_event} but no events fired"
        elif collector.events[0][0] != expected_event:
            passed = False
            reason = f"Expected {expected_event} but got {collector.events[0][0]}"
        elif scenario.get("expected_status"):
            actual_status = collector.events[0][1][0]
            expected = scenario["expected_status"]
            if actual_status != expected:
                passed = False
                reason = f"Expected status {expected} but got {actual_status}"

    if scenario.get("expected_active") is not None:
        if ivr_processor._active != scenario["expected_active"]:
            passed = False
            reason = f"Expected active={scenario['expected_active']} but got {ivr_processor._active}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
        "active": ivr_processor._active,
    }


async def run_dtmf(scenario: dict, flow_config: dict) -> dict:
    """Test DTMF key press."""
    collector = EventCollector()
    ivr_processor = IVRNavigationProcessor()

    ivr_processor.add_event_handler(IVREvent.DTMF_PRESSED, collector.handler(IVREvent.DTMF_PRESSED))
    ivr_processor._active = True

    await ivr_processor._handle_dtmf_action(MockMatch(scenario["dtmf_key"]))
    await asyncio.sleep(0.05)

    passed = True
    reason = ""

    if len(collector.events) == 0:
        passed = False
        reason = f"Expected {IVREvent.DTMF_PRESSED} but no events fired"
    elif collector.events[0][0] != IVREvent.DTMF_PRESSED:
        passed = False
        reason = f"Expected {IVREvent.DTMF_PRESSED} but got {collector.events[0][0]}"
    elif collector.events[0][1][0] != scenario["expected_key"]:
        passed = False
        reason = f"Expected key {scenario['expected_key']} but got {collector.events[0][1][0]}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
    }


async def run_dtmf_sequence(scenario: dict, flow_config: dict) -> dict:
    """Test multiple DTMF key presses."""
    collector = EventCollector()
    ivr_processor = IVRNavigationProcessor()

    ivr_processor.add_event_handler(IVREvent.DTMF_PRESSED, collector.handler(IVREvent.DTMF_PRESSED))
    ivr_processor._active = True

    for key in scenario["dtmf_sequence"]:
        await ivr_processor._handle_dtmf_action(MockMatch(key))

    await asyncio.sleep(0.05)

    passed = True
    reason = ""

    if len(collector.events) != scenario["expected_events"]:
        passed = False
        reason = f"Expected {scenario['expected_events']} events but got {len(collector.events)}"
    else:
        actual_keys = [e[1][0] for e in collector.events]
        if actual_keys != scenario["expected_keys"]:
            passed = False
            reason = f"Expected keys {scenario['expected_keys']} but got {actual_keys}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
    }


async def run_dtmf_then_status(scenario: dict, flow_config: dict) -> dict:
    """Test DTMF followed by status change."""
    collector = EventCollector()
    ivr_processor = IVRNavigationProcessor()

    ivr_processor.add_event_handler(IVREvent.DTMF_PRESSED, collector.handler(IVREvent.DTMF_PRESSED))
    ivr_processor.add_event_handler(IVREvent.STATUS_CHANGED, collector.handler(IVREvent.STATUS_CHANGED))
    ivr_processor._active = True

    await ivr_processor._handle_dtmf_action(MockMatch(scenario["dtmf_key"]))
    await ivr_processor._handle_ivr_action(MockMatch(scenario["ivr_status"]))
    await asyncio.sleep(0.05)

    passed = True
    reason = ""

    expected_events = scenario["expected_events"]
    if len(collector.events) != len(expected_events):
        passed = False
        reason = f"Expected {len(expected_events)} events but got {len(collector.events)}"
    else:
        for i, expected in enumerate(expected_events):
            actual_event = collector.events[i][0]
            actual_value = collector.events[i][1][0]
            if actual_event != expected["event"]:
                passed = False
                reason = f"Event {i}: expected {expected['event']} but got {actual_event}"
                break
            if str(actual_value) != str(expected["value"]):
                passed = False
                reason = f"Event {i}: expected value {expected['value']} but got {actual_value}"
                break

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
    }


async def run_classification_edge(scenario: dict, flow_config: dict) -> dict:
    """Test classification edge cases."""
    collector = EventCollector()
    mock_llm = MockLLMService()

    triage = TriageDetector(
        classifier_llm=mock_llm,
        classifier_prompt=flow_config["classifier_prompt"],
    )

    triage.add_event_handler(TriageEvent.IVR_DETECTED, collector.handler(TriageEvent.IVR_DETECTED))
    triage.add_event_handler(TriageEvent.CONVERSATION_DETECTED, collector.handler(TriageEvent.CONVERSATION_DETECTED))
    triage.add_event_handler(TriageEvent.VOICEMAIL_DETECTED, collector.handler(TriageEvent.VOICEMAIL_DETECTED))

    await triage._triage_processor._process_classification(scenario["classifier_response"])
    await asyncio.sleep(0.05)

    passed = True
    reason = ""

    expected_event = scenario.get("expected_event")
    if expected_event is None:
        if len(collector.events) > 0:
            passed = False
            reason = f"Expected no events but got {collector.events[0][0]}"
    else:
        if len(collector.events) == 0:
            passed = False
            reason = f"Expected {expected_event} but no events fired"
        elif collector.events[0][0] != expected_event:
            passed = False
            reason = f"Expected {expected_event} but got {collector.events[0][0]}"

    if scenario.get("expected_decision_made") is not None:
        actual = triage._triage_processor._decision_made
        if actual != scenario["expected_decision_made"]:
            passed = False
            reason = f"Expected decision_made={scenario['expected_decision_made']} but got {actual}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
        "decision_made": triage._triage_processor._decision_made,
    }


async def run_classification_only_once(scenario: dict, flow_config: dict) -> dict:
    """Test that classification only fires once."""
    collector = EventCollector()
    mock_llm = MockLLMService()

    triage = TriageDetector(
        classifier_llm=mock_llm,
        classifier_prompt=flow_config["classifier_prompt"],
    )

    triage.add_event_handler(TriageEvent.IVR_DETECTED, collector.handler(TriageEvent.IVR_DETECTED))
    triage.add_event_handler(TriageEvent.CONVERSATION_DETECTED, collector.handler(TriageEvent.CONVERSATION_DETECTED))

    for response in scenario["classifier_responses"]:
        await triage._triage_processor._process_classification(response)

    await asyncio.sleep(0.05)

    passed = True
    reason = ""

    if len(collector.events) != scenario["expected_event_count"]:
        passed = False
        reason = f"Expected {scenario['expected_event_count']} events but got {len(collector.events)}"
    elif collector.events[0][0] != scenario["expected_event"]:
        passed = False
        reason = f"Expected {scenario['expected_event']} but got {collector.events[0][0]}"

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "events": [(e[0], str(e[1])) for e in collector.events],
    }


async def run_flow_config(scenario: dict, flow_config: dict) -> dict:
    """Test flow configuration."""
    passed = True
    reason = ""

    for key in scenario["expected_keys"]:
        if key not in flow_config:
            passed = False
            reason = f"Missing key: {key}"
            break

    if passed and "expected_in_classifier_prompt" in scenario:
        for text in scenario["expected_in_classifier_prompt"]:
            if text not in flow_config["classifier_prompt"]:
                passed = False
                reason = f"Classifier prompt missing: {text}"
                break

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "config_keys": list(flow_config.keys()),
    }


async def run_voicemail_template(scenario: dict, flow_config: dict) -> dict:
    """Test voicemail message templating."""
    patient_data = {
        "patient_name": scenario["patient_name"],
        "facility_name": scenario["facility_name"],
    }
    flow = EligibilityVerificationFlow(patient_data=patient_data)
    config = flow.get_triage_config()

    passed = True
    reason = ""

    for text in scenario["expected_in_message"]:
        if text not in config["voicemail_message"]:
            passed = False
            reason = f"Voicemail message missing: {text}"
            break

    if passed and not reason:
        reason = "All checks passed"

    return {
        "passed": passed,
        "reason": reason,
        "voicemail_message": config["voicemail_message"],
    }


# =============================================================================
# SCENARIO ROUTING
# =============================================================================

TEST_RUNNERS = {
    "classification_to_ivr": run_classification_to_ivr,
    "classification_to_ivr_with_history": run_classification_to_ivr,
    "classification_to_conversation": run_classification_to_conversation,
    "classification_to_conversation_with_history": run_classification_to_conversation,
    "classification_to_voicemail": run_classification_to_voicemail,
    "ivr_status": run_ivr_status,
    "dtmf": run_dtmf,
    "dtmf_sequence": run_dtmf_sequence,
    "dtmf_then_status": run_dtmf_then_status,
    "classification_edge": run_classification_edge,
    "classification_only_once": run_classification_only_once,
    "flow_config": run_flow_config,
    "voicemail_template": run_voicemail_template,
}


# =============================================================================
# FLOW CONFIG
# =============================================================================

def get_flow_config() -> dict:
    """Get triage config from EligibilityVerificationFlow."""
    patient_data = {
        "patient_name": "John Doe",
        "date_of_birth": "01/15/1980",
        "insurance_member_id": "ABC123456",
        "cpt_code": "99213",
        "provider_npi": "1234567890",
        "facility_name": "Test Clinic",
    }
    flow = EligibilityVerificationFlow(patient_data=patient_data)
    return flow.get_triage_config()


# =============================================================================
# RESULT SAVING
# =============================================================================

def save_result(scenario_id: str, result: dict) -> Path:
    """Save result to local files."""
    results_dir = Path(__file__).parent / "results" / scenario_id
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    result_file = results_dir / f"{timestamp}.json"

    # Save human-readable version too
    txt_file = results_dir / f"{timestamp}.txt"
    with open(txt_file, "w") as f:
        f.write(f"SCENARIO: {scenario_id}\n")
        f.write(f"RESULT: {'PASS' if result['passed'] else 'FAIL'}\n")
        f.write(f"REASON: {result['reason']}\n")
        f.write(f"{'='*60}\n")
        for key, value in result.items():
            if key not in ("passed", "reason"):
                f.write(f"{key}: {value}\n")

    output = {
        "id": f"{scenario_id}_{timestamp}",
        "scenario_id": scenario_id,
        "timestamp": datetime.now().isoformat(),
        "result": result,
    }

    with open(result_file, "w") as f:
        json.dump(output, f, indent=2)

    return result_file


# =============================================================================
# MAIN
# =============================================================================

async def run_scenario(scenario_id: str) -> dict:
    """Run a single scenario."""
    scenario = get_scenario(SCENARIOS_PATH, scenario_id)
    flow_config = get_flow_config()

    print(f"\n{'='*60}")
    print(f"SCENARIO {scenario_id}: {scenario['description']}")
    print(f"TYPE: {scenario['test_type']}")
    print(f"{'='*60}\n")

    runner = TEST_RUNNERS.get(scenario["test_type"])
    if not runner:
        raise ValueError(f"Unknown test_type: {scenario['test_type']}")

    result = await runner(scenario, flow_config)

    status = "PASS" if result["passed"] else "FAIL"
    print(f"{status} | {scenario_id}")
    print(f"  {result['reason']}")

    result_file = save_result(scenario_id, result)
    print(f"Saved: {result_file}")

    return {"scenario_id": scenario_id, **result}


async def run_all_scenarios() -> list[dict]:
    """Run all scenarios."""
    config = load_scenarios(SCENARIOS_PATH)
    results = []

    for scenario in config["scenarios"]:
        result = await run_scenario(scenario["id"])
        results.append(result)

    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(passed)}/{len(results)} passed")
    print(f"{'='*60}")

    if failed:
        print("\nFAILED:")
        for r in failed:
            print(f"  - {r['scenario_id']}: {r['reason']}")

    return results


async def main():
    parser = argparse.ArgumentParser(description="Prior Auth Triage Integration Evaluation")
    parser.add_argument("--scenario", "-s", help="Run specific scenario by ID")
    parser.add_argument("--all", "-a", action="store_true", help="Run all scenarios")
    parser.add_argument("--list", "-l", action="store_true", help="List available scenarios")

    args = parser.parse_args()

    if args.list:
        list_scenarios(SCENARIOS_PATH)
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
