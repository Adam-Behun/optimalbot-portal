"""
Shared utilities for triage evaluations.

Used by:
- classification/run.py
- ivr_navigation/run.py
- integration/run.py
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from anthropic import Anthropic

from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


# =============================================================================
# EVENT COLLECTION
# =============================================================================

class EventCollector:
    """Collects events fired by processors for verification.

    Usage:
        collector = EventCollector()
        processor.add_event_handler("on_ivr_detected", collector.handler("on_ivr_detected"))
        # ... run test ...
        assert collector.has_event("on_ivr_detected")
    """

    def __init__(self):
        self.events: list[tuple[str, tuple[Any, ...]]] = []

    def handler(self, event_name: str):
        """Returns an async handler that records events."""
        async def _handler(processor, *args, **kwargs):
            # Pipecat passes processor as first arg, then event-specific args
            self.events.append((event_name, args))
        return _handler

    def clear(self):
        """Clear collected events."""
        self.events = []

    def has_event(self, event_name: str, *expected_args) -> bool:
        """Check if event was fired, optionally with specific args."""
        for name, args in self.events:
            if name == event_name:
                if not expected_args:
                    return True
                if args == expected_args:
                    return True
        return False

    def get_event_args(self, event_name: str) -> tuple[Any, ...] | None:
        """Get the arguments of the first matching event."""
        for name, args in self.events:
            if name == event_name:
                return args
        return None

    def count(self, event_name: str = None) -> int:
        """Count events, optionally filtered by name."""
        if event_name is None:
            return len(self.events)
        return sum(1 for name, _ in self.events if name == event_name)


# =============================================================================
# FRAME COLLECTION (for gate tests)
# =============================================================================

class FrameCollector(FrameProcessor):
    """Collects frames pushed by processors for verification.

    Place at the end of a processor chain to capture output frames.

    Usage:
        collector = FrameCollector()
        gate._set_output_processor(collector)  # Wire gate -> collector
        await gate.process_frame(some_frame, DOWNSTREAM)
        assert len(collector.frames) == 0  # Frame was blocked
    """

    def __init__(self):
        super().__init__()
        self.frames: list[tuple[Frame, FrameDirection]] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Capture frame instead of processing."""
        await super().process_frame(frame, direction)
        self.frames.append((frame, direction))

    def clear(self):
        """Clear collected frames."""
        self.frames = []

    def get_frames(self, frame_type: type = None) -> list[Frame]:
        """Get collected frames, optionally filtered by type."""
        if frame_type is None:
            return [f for f, _ in self.frames]
        return [f for f, _ in self.frames if isinstance(f, frame_type)]

    def count(self, frame_type: type = None) -> int:
        """Count frames, optionally filtered by type."""
        return len(self.get_frames(frame_type))


# =============================================================================
# MOCK HELPERS
# =============================================================================

class MockMatch:
    """Mock pattern match result for IVR processor tests.

    The PatternPairAggregator returns match objects with a .content attribute.
    """
    def __init__(self, content: str):
        self.content = content


# =============================================================================
# SCENARIO LOADING
# =============================================================================

def load_scenarios(scenarios_path: Path) -> dict:
    """Load scenarios from YAML file.

    Args:
        scenarios_path: Path to scenarios.yaml

    Returns:
        Dict with 'scenarios' list and optional metadata
    """
    with open(scenarios_path) as f:
        return yaml.safe_load(f)


def get_scenario(scenarios_path: Path, scenario_id: str) -> dict:
    """Get a specific scenario by ID.

    Args:
        scenarios_path: Path to scenarios.yaml
        scenario_id: ID to find

    Returns:
        Scenario dict

    Raises:
        ValueError: If scenario not found
    """
    config = load_scenarios(scenarios_path)
    for scenario in config["scenarios"]:
        if scenario["id"] == scenario_id:
            return scenario
    raise ValueError(f"Scenario '{scenario_id}' not found")


def list_scenarios(scenarios_path: Path) -> None:
    """Print available scenarios to stdout."""
    config = load_scenarios(scenarios_path)
    print("\nAvailable scenarios:\n")
    for s in config["scenarios"]:
        test_type = s.get("test_type", "unknown")
        print(f"  {s['id']:<5} [{test_type}]")
        if "description" in s:
            print(f"        {s['description']}\n")


# =============================================================================
# RESULT SAVING
# =============================================================================

def save_result(
    results_dir: Path,
    scenario_id: str,
    result: dict,
    grade: dict = None,
    trace_id: str = None,
) -> tuple[Path, Path]:
    """Save evaluation result to JSON and TXT files.

    Args:
        results_dir: Base directory for results (e.g., Path(__file__).parent / "results")
        scenario_id: Scenario identifier
        result: Test result dict (must have 'passed' and 'reason' keys)
        grade: Optional LLM grading result
        trace_id: Optional Langfuse trace ID

    Returns:
        Tuple of (json_path, txt_path)
    """
    scenario_dir = results_dir / scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # JSON output
    json_file = scenario_dir / f"{timestamp}.json"
    output = {
        "id": f"{scenario_id}_{timestamp}",
        "scenario_id": scenario_id,
        "timestamp": datetime.now().isoformat(),
        "result": result,
    }
    if grade:
        output["grade"] = grade
    if trace_id:
        output["langfuse_trace_id"] = trace_id
        output["langfuse_trace_url"] = f"https://cloud.langfuse.com/trace/{trace_id}"

    with open(json_file, "w") as f:
        json.dump(output, f, indent=2)

    # Human-readable TXT output
    txt_file = scenario_dir / f"{timestamp}.txt"
    with open(txt_file, "w") as f:
        f.write(f"SCENARIO: {scenario_id}\n")
        f.write(f"RESULT: {'PASS' if result.get('passed') else 'FAIL'}\n")
        f.write(f"REASON: {result.get('reason', 'N/A')}\n")
        f.write(f"{'='*60}\n")
        for key, value in result.items():
            if key not in ("passed", "reason"):
                f.write(f"{key}: {value}\n")
        if grade:
            f.write(f"\nGRADE: {'PASS' if grade.get('pass') else 'FAIL'}\n")
            f.write(f"GRADE REASON: {grade.get('reason', 'N/A')}\n")

    return json_file, txt_file


# =============================================================================
# LLM GRADING
# =============================================================================

def call_grader(prompt: str, model: str = "claude-sonnet-4-20250514", max_tokens: int = 100) -> str:
    """Call Claude for nuanced grading of test results.

    Args:
        prompt: Grading prompt with context and criteria
        model: Claude model to use
        max_tokens: Max response tokens

    Returns:
        Model response text (typically "PASS: reason" or "FAIL: reason")
    """
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def grade_pass_fail(response: str) -> tuple[bool, str]:
    """Parse a PASS/FAIL response from the grader.

    Args:
        response: Grader response (e.g., "PASS: Correct classification")

    Returns:
        Tuple of (passed: bool, reason: str)
    """
    passed = response.upper().startswith("PASS")
    return passed, response
