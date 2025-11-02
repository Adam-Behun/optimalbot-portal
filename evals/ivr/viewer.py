#!/usr/bin/env python3
import argparse
import json
import webbrowser
from pathlib import Path


def load_latest_results():
    results_dir = Path(__file__).parent / "results"
    graded_files = sorted(results_dir.glob("ivr_navigation_graded_*.json"), reverse=True)
    if not graded_files:
        raise FileNotFoundError("No graded results found")

    with open(graded_files[0], 'r') as f:
        data = json.load(f)
    return data, graded_files[0].name


def generate_scenario_html(scenario):
    case_id = scenario.get("case_id", "unknown")
    status = "PASS" if scenario.get("grades", {}).get("overall_pass") else "FAIL"

    html = f"<h3>[{status}] {case_id}</h3>\n"

    html += "<details open>\n<summary>Conversation</summary>\n<pre>\n"

    if "prompts" in scenario and "system" in scenario["prompts"]:
        html += f"SYSTEM:\n{scenario['prompts']['system']}\n\n"

    for msg in scenario.get("conversation_history", []):
        role = msg.get("role", "").upper()
        content = msg.get("content", "")
        html += f"{role}:\n{content}\n\n"

    html += "</pre>\n</details>\n"

    html += "<details>\n<summary>Grading</summary>\n<pre>\n"
    for i, grade in enumerate(scenario.get("grades", {}).get("step_grades", []), 1):
        html += f"Step {i}: {grade.get('score', 'N/A')}\n"
        html += f"  {grade.get('reasoning', 'N/A')}\n\n"
    html += "</pre>\n</details>\n"

    html += "<details>\n<summary>Prompts</summary>\n<pre>\n"
    for i, grade in enumerate(scenario.get("grades", {}).get("step_grades", []), 1):
        if "grading_prompt" in grade:
            html += f"Step {i} Grading Prompt:\n{grade['grading_prompt']}\n\n"
    html += "</pre>\n</details>\n"

    html += f"<p>Total Latency: {scenario.get('total_latency_ms', 'N/A')}ms</p>\n"
    html += "<hr>\n"

    return html


def generate_html(data, filename):
    results = data.get("results", [])
    metrics = data.get("aggregate_metrics", {})

    html = "<!DOCTYPE html>\n<html>\n<head>\n<meta charset='UTF-8'>\n"
    html += f"<title>IVR Results - {filename}</title>\n"
    html += "</head>\n<body>\n"

    html += f"<h1>IVR Navigation Results</h1>\n"
    html += f"<p>{filename}</p>\n"

    if metrics:
        acc = metrics.get("accuracy_metrics", {})
        overall = acc.get("overall_pass_rate", {})
        html += f"<p>Pass Rate: {overall.get('passed', 0)}/{overall.get('total', 0)} ({overall.get('percentage', 0)}%)</p>\n"

    html += "<hr>\n"

    for scenario in results:
        html += generate_scenario_html(scenario)

    html += "</body>\n</html>"
    return html


def main():
    parser = argparse.ArgumentParser(description="View IVR test results")
    parser.add_argument("--latest", action="store_true", help="Open latest results in browser")
    parser.add_argument("--file", type=str, help="Specific results file to view")
    args = parser.parse_args()

    if args.latest:
        data, filename = load_latest_results()
        html_content = generate_html(data, filename)

        output_file = Path(__file__).parent / "results" / "latest_view.html"
        with open(output_file, 'w') as f:
            f.write(html_content)

        webbrowser.open(f"file://{output_file.absolute()}")
        print(f"viewer: opened {output_file.name}")
    elif args.file:
        with open(args.file, 'r') as f:
            data = json.load(f)
        filename = Path(args.file).name
        html_content = generate_html(data, filename)

        output_file = Path(args.file).parent / "view.html"
        with open(output_file, 'w') as f:
            f.write(html_content)

        webbrowser.open(f"file://{output_file.absolute()}")
        print(f"viewer: opened {output_file.name}")


if __name__ == "__main__":
    main()
