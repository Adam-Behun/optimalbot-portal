#!/usr/bin/env python3
"""
LLM Evaluation Runner - CLI for running prompt evaluations.

Usage:
    # Generate test cases
    python evals/llm/run.py --generate --state ivr_navigation --num-cases 5

    # Run evaluation
    python evals/llm/run.py --run --test-file evals/llm/test_cases/ivr_navigation.json

    # Run and grade
    python evals/llm/run.py --run --grade --test-file evals/llm/test_cases/ivr_navigation.json

    # Quick start (all-in-one)
    python evals/llm/run.py --quick-start --state ivr_navigation --num-cases 3
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evals.llm.test_generator import TestDataGenerator, SAMPLE_PATIENT_DATA
from evals.llm.framework import LLMEvaluationFramework
from evals.llm.graders import ResponseGrader, calculate_aggregate_metrics


def generate_test_cases(state: str, num_cases: int, force: bool = False) -> tuple:
    """Generate synthetic test cases for a state."""
    output_dir = Path(__file__).parent / "test_cases"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"{state}.json"

    if output_file.exists() and not force:
        print(f"\n✓ Using existing test cases from {output_file}")
        with open(output_file, 'r') as f:
            test_cases = json.load(f)
        print(f"✓ Loaded {len(test_cases)} existing test cases")
        return test_cases, str(output_file)

    print(f"\nGenerating {num_cases} test cases for '{state}' state...")

    generator = TestDataGenerator()

    if state == "greeting":
        test_scenarios = generator.generate_greeting_test_cases(num_cases)
    elif state == "verification":
        test_scenarios = generator.generate_verification_test_cases(num_cases)
    else:
        raise ValueError(f"Unknown state: {state}")

    # Use sample patient data
    patient_data = SAMPLE_PATIENT_DATA

    # Build full test cases
    test_cases = []
    for scenario in test_scenarios:
        test_case = {
            "state": state,
            "patient_data": patient_data,
            "test_scenario": scenario,
            "conversation_history": [],
        }
        test_cases.append(test_case)

    # Save to file
    with open(output_file, 'w') as f:
        json.dump(test_cases, f, indent=2)

    print(f"✓ Generated {len(test_cases)} test cases")
    print(f"✓ Saved to {output_file}")

    return test_cases, str(output_file)


def run_evaluation(test_file: str) -> tuple:
    """Run evaluation on test cases."""
    print(f"\n🔄 Running evaluation on test cases from {test_file}...")

    # Load test cases
    with open(test_file, 'r') as f:
        test_cases = json.load(f)

    print(f"✓ Loaded {len(test_cases)} test cases")

    # Initialize framework
    framework = LLMEvaluationFramework(client_name="prior_auth")

    # Run evaluation
    results = framework.evaluate_batch(test_cases)

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    state = test_cases[0]["state"] if test_cases else "unknown"

    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"{state}_results_{timestamp}.json"

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Results saved to {output_file}")

    return results, str(output_file)


def grade_results(results: List[Dict[str, Any]], output_file: str) -> tuple:
    """Grade evaluation results."""
    print(f"\n🔄 Grading {len(results)} evaluation results...")
    print("=" * 60)

    grader = ResponseGrader()

    graded_results = []
    for i, result in enumerate(results, 1):
        scenario_id = result.get('scenario_id', 'unknown')
        print(f"\n[{i}/{len(results)}] Grading: {scenario_id}")

        grades = grader.grade_all(result)

        graded_result = {**result, "grades": grades}
        graded_results.append(graded_result)

        # Print summary
        status = "✓ PASS" if grades["overall_pass"] else "✗ FAIL"
        print(f"  {status}")
        print(f"    Info: {grades['information_correctness']['score']}")
        print(f"    Quality: {grades['conversational_quality']['score']}")
        print(f"    Task: {grades['task_completion']['score']}")

    # Calculate aggregate metrics
    print("\n" + "=" * 60)
    print("AGGREGATE METRICS")
    print("=" * 60)

    metrics = calculate_aggregate_metrics(graded_results)

    print(f"\n📊 Accuracy Metrics:")
    acc = metrics['accuracy_metrics']
    print(f"  Information Correctness: {acc['information_correctness']['passed']}/{acc['information_correctness']['total']} ({acc['information_correctness']['percentage']}%)")
    print(f"  Conversational Quality:  {acc['conversational_quality']['passed']}/{acc['conversational_quality']['total']} ({acc['conversational_quality']['percentage']}%)")
    print(f"  Task Completion:         {acc['task_completion']['passed']}/{acc['task_completion']['total']} ({acc['task_completion']['percentage']}%)")
    print(f"  Overall Pass Rate:       {acc['overall_pass_rate']['passed']}/{acc['overall_pass_rate']['total']} ({acc['overall_pass_rate']['percentage']}%)")

    print(f"\n⚡ Latency Metrics:")
    lat = metrics['latency_metrics']
    print(f"  Average: {lat['average_ms']}ms")
    print(f"  Min: {lat['min_ms']}ms")
    print(f"  Max: {lat['max_ms']}ms")
    print(f"  Under 1000ms: {lat['under_1000ms']['count']}/{lat['under_1000ms']['total']} ({lat['under_1000ms']['percentage']}%)")

    # Save graded results
    graded_output_file = output_file.replace("_results_", "_graded_")
    with open(graded_output_file, 'w') as f:
        json.dump({
            "results": graded_results,
            "aggregate_metrics": metrics,
        }, f, indent=2)

    print(f"\n✓ Saved graded results to {graded_output_file}")

    return graded_results, metrics


def quick_start(state: str, num_cases: int):
    """Generate, run, and grade in one command."""
    print("\n" + "=" * 60)
    print(f"LLM EVALUATION: {state.upper()}")
    print("=" * 60)

    # Step 1: Generate
    test_cases, test_file = generate_test_cases(state, num_cases)

    # Step 2: Run
    results, results_file = run_evaluation(test_file)

    # Step 3: Grade
    graded_results, metrics = grade_results(results, results_file)

    print("\n" + "=" * 60)
    print("✓ EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Test Cases: {test_file}")
    print(f"Results: {results_file.replace('_results_', '_graded_')}")


def main():
    parser = argparse.ArgumentParser(description="LLM Evaluation Runner")

    # Modes
    parser.add_argument("--generate", action="store_true", help="Generate test cases")
    parser.add_argument("--run", action="store_true", help="Run evaluation")
    parser.add_argument("--grade", action="store_true", help="Grade results")
    parser.add_argument("--quick-start", action="store_true", help="Generate + Run + Grade")

    # Parameters
    parser.add_argument("--state", type=str, help="State to test (greeting, verification)")
    parser.add_argument("--num-cases", type=int, default=5, help="Number of test cases")
    parser.add_argument("--test-file", type=str, help="Path to test cases JSON")
    parser.add_argument("--results-file", type=str, help="Path to results JSON")

    args = parser.parse_args()

    # Validate
    if not any([args.generate, args.run, args.grade, args.quick_start]):
        parser.error("Must specify mode: --generate, --run, --grade, or --quick-start")

    if args.quick_start:
        if not args.state:
            parser.error("--quick-start requires --state")
        quick_start(args.state, args.num_cases)

    elif args.generate:
        if not args.state:
            parser.error("--generate requires --state")
        generate_test_cases(args.state, args.num_cases)

    elif args.run:
        if not args.test_file:
            parser.error("--run requires --test-file")
        results, results_file = run_evaluation(args.test_file)

        if args.grade:
            grade_results(results, results_file)

    elif args.grade:
        if not args.results_file:
            parser.error("--grade requires --results-file")
        with open(args.results_file, 'r') as f:
            results = json.load(f)
        grade_results(results, args.results_file)


if __name__ == "__main__":
    main()
