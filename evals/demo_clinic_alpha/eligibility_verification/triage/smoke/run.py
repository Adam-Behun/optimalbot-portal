"""
Eligibility Verification Triage Smoke Tests

Pre-deploy validation using REAL services (Groq, OpenAI).
Runs a few key scenarios through the actual pipeline to catch wiring issues.

This is NOT for development iteration - use the isolated evals for that.
Run this before deployment to verify the full stack works.

Requires:
    - GROQ_API_KEY
    - OPENAI_API_KEY

Usage:
    python run.py              # Run all smoke tests
    python run.py --scenario 1 # Run specific scenario
    python run.py --list       # List scenarios
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

from loguru import logger

from pipecat.frames.frames import TranscriptionFrame, EndFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService

from pipeline.triage_detector import TriageDetector
from pipeline.ivr_navigation_processor import IVRNavigationProcessor, IVRStatus
from clients.demo_clinic_alpha.eligibility_verification.flow_definition import EligibilityVerificationFlow


# =============================================================================
# SMOKE TEST SCENARIOS
# =============================================================================

SCENARIOS = [
    {
        "id": "1",
        "name": "IVR Detection",
        "description": "Verify classifier detects IVR menu",
        "transcription": "Thank you for calling Blue Cross Blue Shield. For claims, press 1. For eligibility and benefits, press 2. For prior authorization, press 3.",
        "expected_classification": "IVR",
    },
    {
        "id": "2",
        "name": "Human Detection",
        "description": "Verify classifier detects human conversation",
        "transcription": "Hello, this is Jennifer from the eligibility department. How can I help you today?",
        "expected_classification": "CONVERSATION",
    },
    {
        "id": "3",
        "name": "Voicemail Detection",
        "description": "Verify classifier detects voicemail",
        "transcription": "You've reached the prior authorization department. We are currently closed. Please leave a message after the beep.",
        "expected_classification": "VOICEMAIL",
    },
    {
        "id": "4",
        "name": "IVR Navigation - DTMF",
        "description": "Verify navigator selects correct DTMF option",
        "test_type": "ivr_navigation",
        "transcription": "For eligibility verification, press 1. For claims status, press 2. For prior authorization, press 3.",
        "expected_action": "dtmf",
        "expected_key": "1",  # Should pick eligibility (our goal)
    },
    {
        "id": "5",
        "name": "IVR Navigation - Completed",
        "description": "Verify navigator detects when to stop navigating",
        "test_type": "ivr_navigation",
        "transcription": "Please hold while we connect you to the next available eligibility representative. Your estimated wait time is 3 minutes.",
        "expected_action": "completed",
    },
]


# =============================================================================
# TEST RUNNERS
# =============================================================================

class EventCollector:
    """Collects events for verification."""
    def __init__(self):
        self.events: List[tuple] = []

    def handler(self, name: str):
        async def _handler(processor, *args):
            # Pipecat passes processor as first arg, then event-specific args
            self.events.append((name, args))
        return _handler

    def has_event(self, name: str) -> bool:
        return any(e[0] == name for e in self.events)

    def get_event_value(self, name: str):
        """Get the first argument value of an event."""
        for e in self.events:
            if e[0] == name and e[1]:
                return e[1][0]
        return None


async def run_classification_smoke(scenario: dict) -> dict:
    """Run classification through real Groq LLM.

    Tests the LLM classification directly without full pipeline frame processing
    to avoid pipeline lifecycle issues in test context.
    """
    # Check for API key
    if not os.getenv("GROQ_API_KEY"):
        return {"passed": False, "reason": "GROQ_API_KEY not set", "skipped": True}

    # Create real Groq LLM (same as production)
    classifier_llm = GroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.3-70b-versatile",
        temperature=0,
        max_tokens=10,
    )

    # Get flow config
    flow = EligibilityVerificationFlow(patient_data={"patient_name": "Test", "facility_name": "Test Clinic"})
    flow_config = flow.get_triage_config()

    # Build context with classifier prompt and transcription
    context = LLMContext([
        {"role": "system", "content": flow_config["classifier_prompt"]},
        {"role": "user", "content": scenario["transcription"]},
    ])

    # Run classification using the LLM
    response = await classifier_llm.run_inference(context)

    logger.info(f"Classifier response: {response}")

    # Check if response contains expected classification
    expected = scenario["expected_classification"]
    response_upper = (response or "").upper()

    passed = expected in response_upper

    return {
        "passed": passed,
        "reason": f"LLM returned '{response}', expected '{expected}'" if not passed else f"Correct: {response}",
        "llm_response": response,
    }


async def run_ivr_navigation_smoke(scenario: dict) -> dict:
    """Run IVR navigation through real OpenAI LLM."""
    if not os.getenv("OPENAI_API_KEY"):
        return {"passed": False, "reason": "OPENAI_API_KEY not set", "skipped": True}

    collector = EventCollector()

    # Create real OpenAI LLM (same as production)
    navigator_llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o",
        temperature=0,
    )

    # Get IVR navigation goal
    flow = EligibilityVerificationFlow(patient_data={"patient_name": "Test", "facility_name": "Test Clinic"})
    flow_config = flow.get_triage_config()
    ivr_goal = flow_config["ivr_navigation_goal"]

    # Create IVR processor (just for event handling and response parsing)
    ivr_processor = IVRNavigationProcessor()
    ivr_processor.add_event_handler("on_dtmf_pressed", collector.handler("on_dtmf_pressed"))
    ivr_processor.add_event_handler("on_ivr_status_changed", collector.handler("on_ivr_status_changed"))
    ivr_processor._active = True  # Enable without full activate() frame pushing

    # Build navigation messages using same prompt format as processor
    ivr_prompt = IVRNavigationProcessor.IVR_NAVIGATION_PROMPT.format(goal=ivr_goal)
    messages = [
        {"role": "system", "content": ivr_prompt},
        {"role": "user", "content": scenario["transcription"]},
    ]

    # Run inference
    context = LLMContext(messages)
    response = await navigator_llm.run_inference(context)

    logger.info(f"Navigator response: {response}")

    # Parse response for DTMF or status patterns
    import re
    dtmf_match = re.search(r"<dtmf>(\d|\*|#)</dtmf>", response or "")
    ivr_match = re.search(r"<ivr>(completed|stuck|wait)</ivr>", response or "")

    if dtmf_match:
        # Simulate DTMF event
        await ivr_processor._call_event_handler("on_dtmf_pressed", dtmf_match.group(1))
    elif ivr_match:
        status = ivr_match.group(1)
        if status in ("completed", "stuck"):
            await ivr_processor._call_event_handler("on_ivr_status_changed",
                IVRStatus.COMPLETED if status == "completed" else IVRStatus.STUCK)

    await asyncio.sleep(0.1)

    # Check result
    expected_action = scenario.get("expected_action", "")
    passed = False
    reason = ""

    if expected_action == "dtmf":
        if collector.has_event("on_dtmf_pressed"):
            actual_key = collector.get_event_value("on_dtmf_pressed")
            expected_key = scenario.get("expected_key")
            if actual_key == expected_key:
                passed = True
                reason = f"Correct DTMF: {actual_key}"
            else:
                reason = f"Wrong DTMF: expected {expected_key}, got {actual_key}"
        else:
            reason = f"Expected DTMF but got: {[e[0] for e in collector.events]}"

    elif expected_action == "completed":
        if collector.has_event("on_ivr_status_changed"):
            actual_status = collector.get_event_value("on_ivr_status_changed")
            if actual_status == IVRStatus.COMPLETED:
                passed = True
                reason = "Correctly detected completed"
            else:
                reason = f"Wrong status: {actual_status}"
        else:
            reason = f"Expected status change but got: {[e[0] for e in collector.events]}"

    return {
        "passed": passed,
        "reason": reason,
        "llm_response": response,
        "events": [e[0] for e in collector.events],
    }


# =============================================================================
# MAIN
# =============================================================================

async def run_scenario(scenario_id: str):
    """Run a single smoke test scenario."""
    scenario = next((s for s in SCENARIOS if s["id"] == scenario_id), None)
    if not scenario:
        print(f"Error: Scenario {scenario_id} not found")
        return

    print(f"\n{'='*60}")
    print(f"SMOKE TEST {scenario['id']}: {scenario['name']}")
    print(f"{scenario['description']}")
    print(f"{'='*60}")

    test_type = scenario.get("test_type", "classification")

    if test_type == "ivr_navigation":
        result = await run_ivr_navigation_smoke(scenario)
    else:
        result = await run_classification_smoke(scenario)

    if result.get("skipped"):
        print(f"SKIP | {result['reason']}")
    elif result["passed"]:
        print(f"PASS | {result['reason']}")
    else:
        print(f"FAIL | {result['reason']}")

    if result.get("llm_response"):
        print(f"  LLM: {result['llm_response']}")


async def run_all():
    """Run all smoke tests."""
    print("\n" + "="*60)
    print("PRIOR AUTH TRIAGE SMOKE TESTS")
    print("Using real services (Groq, OpenAI)")
    print("="*60)

    results = []

    for scenario in SCENARIOS:
        test_type = scenario.get("test_type", "classification")

        if test_type == "ivr_navigation":
            result = await run_ivr_navigation_smoke(scenario)
        else:
            result = await run_classification_smoke(scenario)

        results.append({
            "id": scenario["id"],
            "name": scenario["name"],
            **result
        })

        status = "SKIP" if result.get("skipped") else ("PASS" if result["passed"] else "FAIL")
        print(f"{status} | {scenario['id']}: {scenario['name']}")

    # Summary
    passed = sum(1 for r in results if r["passed"])
    skipped = sum(1 for r in results if r.get("skipped"))
    total = len(results) - skipped

    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed}/{total} passed ({skipped} skipped)")

    if passed < total:
        print("\nFailed:")
        for r in results:
            if not r["passed"] and not r.get("skipped"):
                print(f"  {r['id']}: {r['reason']}")


def list_scenarios():
    """List available smoke test scenarios."""
    print("\nSmoke Test Scenarios:\n")
    for s in SCENARIOS:
        test_type = s.get("test_type", "classification")
        print(f"  {s['id']}: {s['name']} [{test_type}]")
        print(f"     {s['description']}\n")


async def main():
    parser = argparse.ArgumentParser(description="Pre-deploy Smoke Tests")
    parser.add_argument("--scenario", "-s", help="Run specific scenario")
    parser.add_argument("--list", "-l", action="store_true", help="List scenarios")

    args = parser.parse_args()

    if args.list:
        list_scenarios()
    elif args.scenario:
        await run_scenario(args.scenario)
    else:
        await run_all()


if __name__ == "__main__":
    asyncio.run(main())
