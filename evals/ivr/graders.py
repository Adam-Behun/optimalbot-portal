import os
import re
from typing import Dict, Any, List
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()


class IVRNavigationGrader:
    def __init__(self, model: str = "claude-sonnet-4-5"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def grade_step(self, user_utterance: str, llm_response: str, expected_dtmf: str, reasoning: str) -> Dict[str, Any]:
        prompt = f"""Grade this IVR navigation decision.

<menu_options>
{user_utterance}
</menu_options>

<llm_response>
{llm_response}
</llm_response>

<expected_behavior>
Should press <dtmf>{expected_dtmf}</dtmf>. {reasoning}
</expected_behavior>

CORRECT if LLM selected the appropriate option matching expected_behavior.
INCORRECT if LLM selected wrong option or no DTMF tag found.

Output brief reasoning (1-2 sentences) in <reasoning> tags, then <score>correct</score> or <score>incorrect</score>.
"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        completion = response.content[0].text
        score_match = re.search(r"<score>(correct|incorrect)</score>", completion, re.IGNORECASE)
        reasoning_match = re.search(r"<reasoning>(.*?)</reasoning>", completion, re.DOTALL)

        if not score_match:
            raise ValueError(f"Could not extract score from grader response: {completion}")

        return {
            "score": score_match.group(1).lower(),
            "reasoning": reasoning_match.group(1).strip() if reasoning_match else "",
            "grading_prompt": prompt,
        }

    def grade_scenario(self, eval_result: Dict[str, Any]) -> Dict[str, Any]:
        conversation_history = eval_result.get("conversation_history", [])
        step_results = eval_result.get("step_results", [])
        step_grades = []

        for i, step_result in enumerate(step_results):
            user_msg = conversation_history[i * 2]["content"] if i * 2 < len(conversation_history) else ""

            grade = self.grade_step(
                user_utterance=user_msg,
                llm_response=step_result["llm_response"],
                expected_dtmf=step_result["expected_dtmf"],
                reasoning=step_result["reasoning"]
            )
            step_grades.append(grade)

        all_correct = all(g["score"] == "correct" for g in step_grades)

        return {
            "step_grades": step_grades,
            "overall_pass": all_correct,
        }


def calculate_aggregate_metrics(graded_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_scenarios = len(graded_results)
    if total_scenarios == 0:
        return {"error": "No results"}

    total_steps = sum(len(r["grades"]["step_grades"]) for r in graded_results)
    correct_steps = sum(sum(1 for g in r["grades"]["step_grades"] if g["score"] == "correct") for r in graded_results)
    overall_pass = sum(1 for r in graded_results if r["grades"]["overall_pass"])

    latencies = [r["total_latency_ms"] for r in graded_results]
    avg_latency = sum(latencies) / len(latencies)
    under_5s = sum(1 for l in latencies if l < 5000)

    return {
        "total_scenarios": total_scenarios,
        "total_steps": total_steps,
        "accuracy_metrics": {
            "step_accuracy": {"passed": correct_steps, "total": total_steps, "percentage": round(correct_steps / total_steps * 100, 2)},
            "overall_pass_rate": {"passed": overall_pass, "total": total_scenarios, "percentage": round(overall_pass / total_scenarios * 100, 2)},
        },
        "latency_metrics": {
            "average_ms": round(avg_latency, 2),
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
            "under_5000ms": {"count": under_5s, "total": total_scenarios, "percentage": round(under_5s / total_scenarios * 100, 2)},
        },
    }
