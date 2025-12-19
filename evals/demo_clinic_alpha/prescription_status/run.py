"""
Prescription Status Flow Evaluation Runner

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

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

import yaml
from langfuse import Langfuse

from clients.demo_clinic_alpha.prescription_status.flow_definition import PrescriptionStatusFlow


# === LANGFUSE CLIENT ===
langfuse = Langfuse()

SCENARIOS_FILE = Path(__file__).parent / "scenarios.yaml"
RESULTS_DIR = Path(__file__).parent / "results"


def load_scenarios() -> dict:
    """Load scenarios from YAML file."""
    with open(SCENARIOS_FILE) as f:
        return yaml.safe_load(f)


def list_scenarios():
    """Print available scenarios."""
    data = load_scenarios()
    print(f"\nDataset: {data['dataset_name']}")
    print(f"Description: {data['dataset_description']}\n")
    print("Available scenarios:")
    for s in data["scenarios"]:
        print(f"  {s['id']}: {s['target_node']} - {s['expected_problem'][:60]}...")


def sync_dataset():
    """Sync scenarios to Langfuse dataset."""
    data = load_scenarios()
    dataset_name = data["dataset_name"]

    # Create or get dataset
    dataset = langfuse.create_dataset(
        name=dataset_name,
        description=data["dataset_description"]
    )

    for scenario in data["scenarios"]:
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input={"scenario": scenario},
            expected_output={"problem_should_occur": scenario["expected_problem"]},
            metadata={
                "target_node": scenario["target_node"],
                "scenario_id": scenario["id"]
            }
        )

    print(f"Synced {len(data['scenarios'])} scenarios to Langfuse dataset: {dataset_name}")


async def run_scenario(scenario_id: str):
    """Run a single scenario."""
    # TODO: Implement when PrescriptionStatusFlow is complete
    raise NotImplementedError(
        "Prescription Status flow evaluation not yet implemented. "
        "Complete flow_definition.py first."
    )


async def run_all():
    """Run all scenarios."""
    data = load_scenarios()
    for scenario in data["scenarios"]:
        await run_scenario(scenario["id"])


def main():
    parser = argparse.ArgumentParser(description="Prescription Status Flow Evaluation Runner")
    parser.add_argument("--scenario", "-s", help="Run specific scenario by ID")
    parser.add_argument("--all", "-a", action="store_true", help="Run all scenarios")
    parser.add_argument("--list", "-l", action="store_true", help="List available scenarios")
    parser.add_argument("--sync-dataset", action="store_true", help="Sync scenarios to Langfuse")

    args = parser.parse_args()

    if args.list:
        list_scenarios()
    elif args.sync_dataset:
        sync_dataset()
    elif args.all:
        asyncio.run(run_all())
    elif args.scenario:
        asyncio.run(run_scenario(args.scenario))
    else:
        # Run first scenario by default
        data = load_scenarios()
        if data["scenarios"]:
            asyncio.run(run_scenario(data["scenarios"][0]["id"]))
        else:
            print("No scenarios found")


if __name__ == "__main__":
    main()
