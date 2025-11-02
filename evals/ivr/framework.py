import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evals.llm.framework import LLMEvaluationFramework
from pipecat.extensions.ivr.ivr_navigator import IVRNavigator


class IVRNavigationFramework(LLMEvaluationFramework):
    def evaluate_scenario(self, state: str, scenario: Dict[str, Any]) -> Dict[str, Any]:
        ivr_goal = self.prompt_renderer.render_prompt("ivr_navigation", "task", {})
        system_prompt = IVRNavigator.IVR_NAVIGATION_BASE.format(goal=ivr_goal)

        steps = scenario.get("steps", [])
        conversation_history = []
        step_results = []
        total_latency = 0

        for step in steps:
            conversation_history.append({"role": "user", "content": step["user_utterance"]})

            llm_result = self.call_llm(
                system_prompt=system_prompt,
                user_prompt="",
                conversation_history=conversation_history
            )

            conversation_history.append({"role": "assistant", "content": llm_result["response"]})

            step_results.append({
                "expected_dtmf": step["expected_dtmf"],
                "reasoning": step["reasoning"],
                "llm_response": llm_result["response"],
                "latency_ms": llm_result["latency_ms"],
            })

            total_latency += llm_result["latency_ms"]

        return {
            "state": state,
            "scenario_id": scenario.get("scenario_id", ""),
            "description": scenario.get("description", ""),
            "conversation_history": conversation_history,
            "step_results": step_results,
            "total_latency_ms": total_latency,
            "prompts": {"system": system_prompt},
        }
