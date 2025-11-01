#!/usr/bin/env python3
"""Simple viewer for IVR test results - shows what was asked and what the LLM replied."""
import json
import sys
from pathlib import Path


def get_latest_graded_file():
    """Find the most recent graded results file."""
    results_dir = Path(__file__).parent / "results"
    graded_files = sorted(results_dir.glob("ivr_navigation_graded_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not graded_files:
        print("Error: No graded results files found in evals/ivr/results/")
        sys.exit(1)

    return graded_files[0]


def view_results(graded_file: str):
    """Show LLM input/output for each test case."""
    with open(graded_file, 'r') as f:
        data = json.load(f)

    results = data.get("results", [])

    print(f"\n{'='*80}")
    print(f"IVR NAVIGATION TESTS - {len(results)} cases")
    print(f"{'='*80}\n")

    for r in results:
        case_id = r.get("case_id", "???")
        passed = r.get("grades", {}).get("overall_pass", False)
        status = "✓ PASS" if passed else "✗ FAIL"

        print(f"[{case_id}] {status}")
        print(f"Input:  {r.get('user_utterance', '')}")
        print(f"Output: {r.get('llm_response', '')}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "--latest":
        graded_file = get_latest_graded_file()
        print(f"Viewing: {graded_file.name}\n")
    else:
        graded_file = sys.argv[1]

    view_results(graded_file)
