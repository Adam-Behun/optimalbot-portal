#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evals.ivr.test_generator import IVRTestGenerator, SAMPLE_PATIENT_DATA
from evals.ivr.framework import IVRNavigationFramework
from evals.ivr.graders import IVRNavigationGrader, calculate_aggregate_metrics


def _convert_multi_step_to_test_case(scenario: dict, case_id: str) -> list:
    steps = scenario.get("steps", [])
    if len(steps) == 0:
        return []

    test_cases = []

    for step_idx, step in enumerate(steps):
        conversation_history = []

        for prev_step in steps[:step_idx]:
            conversation_history.append({
                "role": "user",
                "content": prev_step["user_utterance"]
            })
            conversation_history.append({
                "role": "assistant",
                "content": f"<dtmf>{prev_step['expected_dtmf']}</dtmf>"
            })

        test_case = {
            "case_id": f"{case_id}_step{step_idx+1}",
            "state": "ivr_navigation",
            "patient_data": SAMPLE_PATIENT_DATA,
            "test_scenario": {
                "scenario_id": scenario["scenario_id"],
                "description": f"{scenario['description']} [Step {step_idx+1}/{len(steps)}]",
                "user_utterance": step["user_utterance"],
                "expected_behavior": f"Should press <dtmf>{step['expected_dtmf']}</dtmf>. {step['reasoning']}",
            },
            "conversation_history": conversation_history,
        }
        test_cases.append(test_case)

    return test_cases


def generate_test_cases(append: bool = False, single: int = 0, multi: int = 0, dead_end: int = 0):
    output_dir = Path(__file__).parent / "test_cases"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "ivr_navigation.json"

    existing_cases = []
    if output_file.exists():
        with open(output_file, 'r') as f:
            existing_cases = json.load(f)
        print(f"Found {len(existing_cases)} existing test cases")

    if not append and existing_cases and (single > 0 or multi > 0 or dead_end > 0):
        print("Use --append to add new cases or --force to regenerate all")
        return existing_cases, str(output_file)

    generator = IVRTestGenerator()
    new_cases = []
    start_id = len(existing_cases)

    current_id = start_id

    if single > 0:
        print(f"Generating {single} single-step cases...")
        scenarios = generator.generate_single_step_cases(single)
        for scenario in scenarios:
            test_case = {
                "case_id": f"{current_id:03d}",
                "state": "ivr_navigation",
                "patient_data": SAMPLE_PATIENT_DATA,
                "test_scenario": scenario,
                "conversation_history": [],
            }
            new_cases.append(test_case)
            current_id += 1

    if multi > 0:
        print(f"Generating {multi} multi-step sequences...")
        scenarios = generator.generate_multi_step_cases(multi)
        for scenario in scenarios:
            multi_step_cases = _convert_multi_step_to_test_case(scenario, f"{current_id:03d}")
            new_cases.extend(multi_step_cases)
            current_id += len(multi_step_cases)

    if dead_end > 0:
        print(f"Generating {dead_end} dead-end scenarios...")
        scenarios = generator.generate_dead_end_cases(dead_end)
        for scenario in scenarios:
            dead_end_cases = _convert_multi_step_to_test_case(scenario, f"{current_id:03d}")
            new_cases.extend(dead_end_cases)
            current_id += len(dead_end_cases)

    if append:
        all_cases = existing_cases + new_cases
        print(f"Appending {len(new_cases)} new cases to {len(existing_cases)} existing")
    else:
        all_cases = new_cases if new_cases else existing_cases

    with open(output_file, 'w') as f:
        json.dump(all_cases, f, indent=2)

    print(f"Total test cases: {len(all_cases)}")
    print(f"Saved to {output_file}")
    return all_cases, str(output_file)


def run_evaluation(test_file: str):
    print(f"\nRunning evaluation on {test_file}")
    with open(test_file, 'r') as f:
        test_cases = json.load(f)

    print(f"Loaded {len(test_cases)} test cases\n")
    print("=" * 60)

    framework = IVRNavigationFramework(client_name="prior_auth")

    results = []
    for i, test_case in enumerate(test_cases, 1):
        case_id = test_case.get('case_id', f'{i-1:03d}')
        scenario_id = test_case['test_scenario'].get('scenario_id', 'unknown')
        print(f"[{case_id}] {scenario_id}")

        result = framework.evaluate_single(
            state=test_case["state"],
            patient_data=test_case["patient_data"],
            test_scenario=test_case["test_scenario"],
            conversation_history=test_case.get("conversation_history"),
        )

        result['case_id'] = case_id
        results.append(result)

        print(f"  Latency: {result['latency_ms']}ms")
        print(f"  Response: {result['llm_response'][:60]}...")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"ivr_navigation_results_{timestamp}.json"

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to {output_file}")
    return results, str(output_file)


def grade_results(results, output_file: str):
    print(f"\nGrading {len(results)} results")
    print("=" * 60)
    grader = IVRNavigationGrader()

    graded_results = []
    for result in results:
        case_id = result.get('case_id', 'unknown')
        scenario_id = result.get('scenario_id', 'unknown')
        desc = result.get('test_scenario', {}).get('description', '')[:40]

        grades = grader.grade_all(result)
        graded_result = {**result, "grades": grades}
        graded_results.append(graded_result)

        status = "PASS" if grades["overall_pass"] else "FAIL"
        print(f"[{case_id}] {status} | {desc}")

    print("\nAGGREGATE METRICS")
    metrics = calculate_aggregate_metrics(graded_results)

    acc = metrics['accuracy_metrics']
    print(f"Navigation Decision: {acc['navigation_decision']['passed']}/{acc['navigation_decision']['total']} ({acc['navigation_decision']['percentage']}%)")
    print(f"Overall Pass Rate: {acc['overall_pass_rate']['passed']}/{acc['overall_pass_rate']['total']} ({acc['overall_pass_rate']['percentage']}%)")

    lat = metrics['latency_metrics']
    print(f"Average Latency: {lat['average_ms']}ms")
    print(f"Under 1000ms: {lat['under_1000ms']['count']}/{lat['under_1000ms']['total']} ({lat['under_1000ms']['percentage']}%)")

    graded_output_file = output_file.replace("_results_", "_graded_")
    with open(graded_output_file, 'w') as f:
        json.dump({"results": graded_results, "aggregate_metrics": metrics}, f, indent=2)

    print(f"Saved graded results to {graded_output_file}")
    return graded_results, metrics


def quick_start(single: int = 3, multi: int = 2, dead_end: int = 1):
    print("IVR NAVIGATION EVALUATION")
    test_cases, test_file = generate_test_cases(append=False, single=single, multi=multi, dead_end=dead_end)
    results, results_file = run_evaluation(test_file)
    grade_results(results, results_file)
    print("EVALUATION COMPLETE")


def main():
    parser = argparse.ArgumentParser(description="IVR Navigation Evaluator")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--grade", action="store_true")
    parser.add_argument("--quick-start", action="store_true")
    parser.add_argument("--append", action="store_true", help="Append to existing test cases")
    parser.add_argument("--single", type=int, default=0, help="Number of single-step cases")
    parser.add_argument("--multi", type=int, default=0, help="Number of multi-step sequences")
    parser.add_argument("--dead-end", type=int, default=0, help="Number of dead-end scenarios")
    parser.add_argument("--test-file", type=str)
    parser.add_argument("--results-file", type=str)

    args = parser.parse_args()

    if args.quick_start:
        quick_start(single=args.single or 3, multi=args.multi or 2, dead_end=args.dead_end or 1)
    elif args.generate:
        generate_test_cases(append=args.append, single=args.single, multi=args.multi, dead_end=args.dead_end)
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
