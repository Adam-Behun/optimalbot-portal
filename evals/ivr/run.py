#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evals.ivr.test_generator import IVRTestGenerator
from evals.ivr.framework import IVRNavigationFramework
from evals.ivr.graders import IVRNavigationGrader, calculate_aggregate_metrics


def generate(append: bool = False, multi: int = 0, dead_end: int = 0):
    output_dir = Path(__file__).parent / "test_cases"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "ivr_navigation.json"

    existing_cases = []
    if output_file.exists() and output_file.stat().st_size > 0:
        with open(output_file, 'r') as f:
            existing_cases = json.load(f)

    if not append and existing_cases and (multi > 0 or dead_end > 0):
        return existing_cases, str(output_file)

    generator = IVRTestGenerator()
    new_cases = []
    start_id = len(existing_cases)

    total_cases = multi + dead_end
    if total_cases > 0:
        scenarios = generator.generate_test_cases(total_cases)
        for i, scenario in enumerate(scenarios):
            new_cases.append({
                "case_id": f"{start_id + i:03d}",
                "state": "ivr_navigation",
                "scenario": scenario
            })

    all_cases = existing_cases + new_cases if append else (new_cases if new_cases else existing_cases)

    with open(output_file, 'w') as f:
        json.dump(all_cases, f, indent=2, ensure_ascii=False)

    print(f"test_generator: {len(all_cases)} scenarios")
    return all_cases, str(output_file)


def run(test_file: str):
    with open(test_file, 'r') as f:
        test_cases = json.load(f)

    framework = IVRNavigationFramework(client_name="prior_auth")
    results = []

    for test_case in test_cases:
        result = framework.evaluate_scenario(
            state=test_case["state"],
            scenario=test_case["scenario"]
        )
        results.append({"case_id": test_case["case_id"], **result})

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"ivr_navigation_results_{timestamp}.json"

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"framework: {len(results)} scenarios evaluated")
    return results, str(output_file)


def grade(results, output_file: str):
    grader = IVRNavigationGrader()
    graded_results = []

    for result in results:
        grades = grader.grade_scenario(result)
        graded_result = {**result, "grades": grades}
        graded_results.append(graded_result)

    metrics = calculate_aggregate_metrics(graded_results)
    graded_output_file = output_file.replace("_results_", "_graded_")

    with open(graded_output_file, 'w') as f:
        json.dump({"results": graded_results, "aggregate_metrics": metrics}, f, indent=2, ensure_ascii=False)

    acc = metrics['accuracy_metrics']
    print(f"grader: {acc['overall_pass_rate']['passed']}/{acc['overall_pass_rate']['total']} passed ({acc['overall_pass_rate']['percentage']}%)")
    return graded_results, metrics


def quick_start(multi: int = 2, dead_end: int = 1):
    test_cases, test_file = generate(append=False, multi=multi, dead_end=dead_end)
    results, results_file = run(test_file)
    grade(results, results_file)


def main():
    parser = argparse.ArgumentParser(description="IVR Navigation Evaluator")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--grade", action="store_true")
    parser.add_argument("--quick-start", action="store_true")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--multi", type=int, default=0)
    parser.add_argument("--dead-end", type=int, default=0)
    parser.add_argument("--test-file", type=str)
    parser.add_argument("--results-file", type=str)

    args = parser.parse_args()

    if args.quick_start:
        quick_start(multi=args.multi or 2, dead_end=args.dead_end or 1)
    elif args.generate:
        generate(append=args.append, multi=args.multi, dead_end=args.dead_end)
    elif args.run:
        if not args.test_file:
            parser.error("--run requires --test-file")
        results, results_file = run(args.test_file)
        if args.grade:
            grade(results, results_file)
    elif args.grade:
        if not args.results_file:
            parser.error("--grade requires --results-file")
        with open(args.results_file, 'r') as f:
            results = json.load(f)
        grade(results, args.results_file)


if __name__ == "__main__":
    main()
