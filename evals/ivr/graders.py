import os
import re
from typing import Dict, Any, List
from anthropic import Anthropic


class IVRNavigationGrader:
    def __init__(self, model: str = "claude-sonnet-4-5"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def _call_grader(self, grader_prompt: str) -> Dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": grader_prompt}],
        )

        completion = response.content[0].text
        score_match = re.search(r"<score>(correct|incorrect)</score>", completion, re.IGNORECASE)
        reasoning_match = re.search(r"<reasoning>(.*?)</reasoning>", completion, re.DOTALL)

        if not score_match:
            raise ValueError(f"Could not extract score from grader response: {completion}")

        return {
            "score": score_match.group(1).lower(),
            "reasoning": reasoning_match.group(1).strip() if reasoning_match else "",
            "raw_response": completion,
        }

    def grade_navigation_decision(
        self,
        llm_response: str,
        menu_options: str,
        expected_behavior: str
    ) -> Dict[str, Any]:
        grader_prompt = f"""You are grading an IVR navigation decision.

<menu_options>
{menu_options}
</menu_options>

<llm_response>
{llm_response}
</llm_response>

<expected_behavior>
{expected_behavior}
</expected_behavior>

GRADING CRITERIA:

CORRECT if the LLM selected the appropriate menu option that matches expected_behavior.
INCORRECT if the LLM selected the wrong option or no DTMF tag found.

Think through your evaluation in <reasoning> tags, then output <score>correct</score> or <score>incorrect</score>.
"""
        return self._call_grader(grader_prompt)

    def grade_all(self, eval_result: Dict[str, Any]) -> Dict[str, Any]:
        nav_grade = self.grade_navigation_decision(
            llm_response=eval_result["llm_response"],
            menu_options=eval_result.get("user_utterance", ""),
            expected_behavior=eval_result.get("expected_behavior", ""),
        )

        return {
            "navigation_decision": nav_grade,
            "overall_pass": nav_grade["score"] == "correct",
        }


def calculate_aggregate_metrics(graded_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(graded_results)
    if total == 0:
        return {"error": "No results to aggregate"}

    nav_correct = sum(1 for r in graded_results if r["grades"]["navigation_decision"]["score"] == "correct")
    overall_pass = sum(1 for r in graded_results if r["grades"]["overall_pass"])

    latencies = [r["latency_ms"] for r in graded_results]
    avg_latency = sum(latencies) / len(latencies)
    under_1s = sum(1 for l in latencies if l < 1000)

    return {
        "total_test_cases": total,
        "accuracy_metrics": {
            "navigation_decision": {
                "passed": nav_correct,
                "total": total,
                "percentage": round(nav_correct / total * 100, 2),
            },
            "overall_pass_rate": {
                "passed": overall_pass,
                "total": total,
                "percentage": round(overall_pass / total * 100, 2),
            },
        },
        "latency_metrics": {
            "average_ms": round(avg_latency, 2),
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
            "under_1000ms": {
                "count": under_1s,
                "total": total,
                "percentage": round(under_1s / total * 100, 2),
            },
        },
    }
