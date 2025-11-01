"""
Model-based grading system for evaluating LLM responses.

Grades conversational states on three metrics:
- Information correctness
- Conversational quality
- Task completion
"""

import os
import re
from typing import Dict, Any, List
from anthropic import Anthropic


class ResponseGrader:
    """
    Uses Claude as a model-based grader to evaluate LLM responses.

    Grades on three metrics for conversational states.
    """

    def __init__(self, model: str = "claude-sonnet-4-5"):
        """Initialize grader with Claude API."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def _call_grader(self, grader_prompt: str) -> Dict[str, Any]:
        """
        Call Claude with grading prompt.

        Returns:
            Dict with 'score' (correct/incorrect) and 'reasoning'
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": grader_prompt}],
        )

        completion = response.content[0].text

        # Extract score and reasoning
        score_match = re.search(r"<score>(correct|incorrect)</score>", completion, re.IGNORECASE)
        reasoning_match = re.search(r"<reasoning>(.*?)</reasoning>", completion, re.DOTALL)

        if not score_match:
            raise ValueError(f"Could not extract score from grader response: {completion}")

        return {
            "score": score_match.group(1).lower(),
            "reasoning": reasoning_match.group(1).strip() if reasoning_match else "",
            "raw_response": completion,
        }

    def grade_information_correctness(
        self,
        llm_response: str,
        patient_data: Dict[str, Any],
        state: str,
        expected_behavior: str = None,
    ) -> Dict[str, Any]:
        """
        Grade whether the response uses accurate patient information without hallucinations.

        Args:
            llm_response: The LLM's response to evaluate
            patient_data: The actual patient data available to the LLM
            state: Current conversation state
            expected_behavior: Optional description of what should happen

        Returns:
            Dict with score, reasoning, and details
        """
        grader_prompt = f"""You are grading an AI assistant's response in a healthcare voice AI system for prior authorization verification.

Your task: Evaluate if the response uses ONLY accurate information from the provided patient data, with NO hallucinations or fabrications.

<patient_data>
{self._format_patient_data(patient_data)}
</patient_data>

<conversation_state>
{state}
</conversation_state>

<llm_response>
{llm_response}
</llm_response>

{"<expected_behavior>" + expected_behavior + "</expected_behavior>" if expected_behavior else ""}

GRADING CRITERIA:

✓ CORRECT if:
- All patient information mentioned (names, dates, IDs, codes) matches the patient_data exactly
- The response only uses information explicitly provided in patient_data
- If information is not available, the response says so rather than making it up
- Formatted data (spelled out IDs, spoken dates) is used appropriately

✗ INCORRECT if:
- ANY patient information is fabricated, guessed, or incorrect
- Names, dates, IDs, or codes don't match the provided data
- The response invents details not in patient_data
- The response provides specific information when it should say "I don't have that information"

Think through your evaluation step-by-step in <reasoning> tags, then output your final grade as either <score>correct</score> or <score>incorrect</score>.
"""

        return self._call_grader(grader_prompt)

    def grade_conversational_quality(
        self,
        llm_response: str,
        state: str,
        user_utterance: str = None,
        expected_behavior: str = None,
    ) -> Dict[str, Any]:
        """
        Grade conversational quality: professional tone, naturalness, appropriateness.

        Args:
            llm_response: The LLM's response to evaluate
            state: Current conversation state
            user_utterance: What the user/insurance rep said
            expected_behavior: Optional description of expected quality

        Returns:
            Dict with score, reasoning, and details
        """
        grader_prompt = f"""You are grading an AI assistant's response in a healthcare voice AI system for prior authorization verification.

Your task: Evaluate the conversational quality, tone, and professionalism of the response.

<conversation_state>
{state}
</conversation_state>

{f"<user_utterance>{user_utterance}</user_utterance>" if user_utterance else ""}

<llm_response>
{llm_response}
</llm_response>

{f"<expected_behavior>{expected_behavior}</expected_behavior>" if expected_behavior else ""}

GRADING CRITERIA:

✓ CORRECT if:
- Maintains professional medical office assistant tone
- Uses complete, grammatically correct sentences
- Keeps responses concise (generally under 30 words for voice)
- Sounds natural and conversational, not robotic
- Stays on topic (insurance verification)
- Appropriate level of formality for healthcare context
- No slang, casual contractions, or inappropriate language

✗ INCORRECT if:
- Unprofessional, overly casual, or inappropriate tone
- Grammatical errors or awkward phrasing
- Excessively verbose responses that would sound unnatural in voice
- Off-topic content (personal chat, medical advice, billing disputes)
- Mirrors caller's informal language instead of maintaining professional tone
- Robotic or scripted-sounding (reads like a template)

Think through your evaluation step-by-step in <reasoning> tags, then output your final grade as either <score>correct</score> or <score>incorrect</score>.
"""

        return self._call_grader(grader_prompt)

    def grade_task_completion(
        self,
        llm_response: str,
        state: str,
        expected_behavior: str,
        patient_data: Dict[str, Any] = None,
        user_utterance: str = None,
    ) -> Dict[str, Any]:
        """
        Grade whether the response correctly follows the expected workflow and completes the task.

        Args:
            llm_response: The LLM's response to evaluate
            state: Current conversation state
            expected_behavior: Description of what the bot SHOULD do
            patient_data: Optional patient data context
            user_utterance: Optional user input

        Returns:
            Dict with score, reasoning, and details
        """
        grader_prompt = f"""You are grading an AI assistant's response in a healthcare voice AI system for prior authorization verification.

Your task: Evaluate if the response correctly follows the expected workflow and completes the required task.

<conversation_state>
{state}
</conversation_state>

{f"<patient_data>{self._format_patient_data(patient_data)}</patient_data>" if patient_data else ""}

{f"<user_utterance>{user_utterance}</user_utterance>" if user_utterance else ""}

<llm_response>
{llm_response}
</llm_response>

<expected_behavior>
{expected_behavior}
</expected_behavior>

GRADING CRITERIA:

✓ CORRECT if:
- The response does what's described in expected_behavior
- Follows the correct workflow for this conversation state
- Makes appropriate decisions based on the context
- Takes initiative when needed (e.g., providing information proactively)
- Responds appropriately to user utterances
- Uses correct DTMF tones for IVR navigation (if applicable)
- Includes necessary function calls or state transitions (if applicable)

✗ INCORRECT if:
- Doesn't do what's described in expected_behavior
- Skips required workflow steps
- Makes incorrect decisions for this state
- Fails to respond appropriately to user input
- Waits when it should act, or acts when it should wait
- Missing required function calls or state transitions
- Performs actions not appropriate for this state

Think through your evaluation step-by-step in <reasoning> tags, then output your final grade as either <score>correct</score> or <score>incorrect</score>.
"""

        return self._call_grader(grader_prompt)

    def grade_all(
        self,
        eval_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run all three graders on an evaluation result.

        Args:
            eval_result: Output from EvalFramework.evaluate_single()

        Returns:
            Dict with all grading results and overall pass/fail
        """
        # Grade information correctness
        info_grade = self.grade_information_correctness(
            llm_response=eval_result["llm_response"],
            patient_data=eval_result["patient_data"],
            state=eval_result["state"],
            expected_behavior=eval_result.get("expected_behavior"),
        )

        # Grade conversational quality
        quality_grade = self.grade_conversational_quality(
            llm_response=eval_result["llm_response"],
            state=eval_result["state"],
            user_utterance=eval_result.get("user_utterance"),
            expected_behavior=eval_result.get("expected_behavior"),
        )

        # Grade task completion
        task_grade = self.grade_task_completion(
            llm_response=eval_result["llm_response"],
            state=eval_result["state"],
            expected_behavior=eval_result.get("expected_behavior", ""),
            patient_data=eval_result["patient_data"],
            user_utterance=eval_result.get("user_utterance"),
        )

        # Calculate overall pass/fail
        all_correct = (
            info_grade["score"] == "correct"
            and quality_grade["score"] == "correct"
            and task_grade["score"] == "correct"
        )

        return {
            "information_correctness": info_grade,
            "conversational_quality": quality_grade,
            "task_completion": task_grade,
            "overall_pass": all_correct,
        }

    def _format_patient_data(self, patient_data: Dict[str, Any]) -> str:
        """Format patient data for display in grader prompts."""
        lines = []
        for key, value in patient_data.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)


def calculate_aggregate_metrics(graded_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate aggregate metrics across multiple graded eval results.

    Args:
        graded_results: List of eval results with grades attached

    Returns:
        Dict with aggregate statistics
    """
    total = len(graded_results)

    if total == 0:
        return {"error": "No results to aggregate"}

    # Count passes for each dimension
    info_correct = sum(1 for r in graded_results if r["grades"]["information_correctness"]["score"] == "correct")
    quality_correct = sum(1 for r in graded_results if r["grades"]["conversational_quality"]["score"] == "correct")
    task_correct = sum(1 for r in graded_results if r["grades"]["task_completion"]["score"] == "correct")
    overall_pass = sum(1 for r in graded_results if r["grades"]["overall_pass"])

    # Calculate latency statistics
    latencies = [r["latency_ms"] for r in graded_results]
    avg_latency = sum(latencies) / len(latencies)
    min_latency = min(latencies)
    max_latency = max(latencies)
    under_1s = sum(1 for l in latencies if l < 1000)

    return {
        "total_test_cases": total,
        "accuracy_metrics": {
            "information_correctness": {
                "passed": info_correct,
                "total": total,
                "percentage": round(info_correct / total * 100, 2),
            },
            "conversational_quality": {
                "passed": quality_correct,
                "total": total,
                "percentage": round(quality_correct / total * 100, 2),
            },
            "task_completion": {
                "passed": task_correct,
                "total": total,
                "percentage": round(task_correct / total * 100, 2),
            },
            "overall_pass_rate": {
                "passed": overall_pass,
                "total": total,
                "percentage": round(overall_pass / total * 100, 2),
            },
        },
        "latency_metrics": {
            "average_ms": round(avg_latency, 2),
            "min_ms": round(min_latency, 2),
            "max_ms": round(max_latency, 2),
            "under_1000ms": {
                "count": under_1s,
                "total": total,
                "percentage": round(under_1s / total * 100, 2),
            },
        },
    }
