"""
LLM Evaluation Framework - Standalone testing for prompt performance.

Follows the exact same workflow as production bot:
1. DataFormatter.format_patient_data() - Format patient data with spoken versions
2. PromptRenderer individual section rendering - Render system/task separately
3. OpenAI API call - Send to LLM with proper message structure
4. Measure latency and collect response

Does NOT use: STT, TTS, Daily transport, Pipecat pipeline, MongoDB writes
"""

import time
import os
import yaml
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

# Import core utilities (same as production bot)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from core.schema_parser import ConversationSchema
from core.data_formatter import DataFormatter
from core.prompt_renderer import PromptRenderer


class LLMEvaluationFramework:
    """
    Standalone framework for testing LLM prompt performance.

    Mimics production bot workflow without voice pipeline.
    """

    def __init__(
        self,
        client_name: str = "prior_auth",
        model: str = "gpt-4o",
        temperature: float = 0.7,
    ):
        self.client_name = client_name
        self.model = model
        self.temperature = temperature

        project_root = Path(__file__).parent.parent.parent
        client_path = project_root / "clients" / client_name
        if not client_path.exists():
            raise ValueError(f"Client directory not found: {client_path}")

        with open(client_path / 'schema.yaml', 'r') as f:
            schema_data = yaml.safe_load(f)

        with open(client_path / 'prompts.yaml', 'r') as f:
            prompts_data = yaml.safe_load(f)

        # Create schema (same as ConversationContext in production)
        self.schema = ConversationSchema(
            base_path=client_path,
            prompts=prompts_data,
            **schema_data
        )

        # Create formatters (same as ConversationContext)
        self.data_formatter = DataFormatter(self.schema)
        self.prompt_renderer = PromptRenderer(self.schema)

        # Initialize OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        self.openai_client = OpenAI(api_key=api_key)

    def format_patient_data(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format patient data with spoken versions.

        Same as ConversationContext.__init__()
        """
        return self.data_formatter.format_patient_data(patient_data)

    def render_prompt_sections(
        self,
        state: str,
        formatted_data: Dict[str, Any],
    ) -> Dict[str, str]:
        """
        Render individual prompt sections for a state.

        Returns dict with 'system' and 'task' keys for OpenAI API structure.
        """
        # Get state definition
        state_def = self.schema.get_state(state)

        # Build context with filtered data access
        context = {
            'voice_name': self.schema.voice.persona.name,
            'voice_role': self.schema.voice.persona.role,
            'client_company': self.schema.voice.persona.client_company,
        }

        # Add only accessible fields for this state (same as PromptRenderer._build_context)
        for field in state_def.data_access:
            if field in formatted_data:
                context[field] = formatted_data[field]
            spoken_field = f"{field}_spoken"
            if spoken_field in formatted_data:
                context[spoken_field] = formatted_data[spoken_field]

        # Safety: add all precomputed fields
        context.update(formatted_data)

        # Render global instructions
        global_text = ""
        if self.prompt_renderer._global_instructions_template:
            global_text = self.prompt_renderer._global_instructions_template.render(**context)

        # Render each section separately
        system_prompt = ""
        task_prompt = ""

        system_key = f"{state}.system"
        if system_key in self.prompt_renderer._cache:
            system_prompt = self.prompt_renderer._cache[system_key].render(**context)
            system_prompt = system_prompt.replace('{{ _global_instructions }}', global_text)

        task_key = f"{state}.task"
        if task_key in self.prompt_renderer._cache:
            task_prompt = self.prompt_renderer._cache[task_key].render(**context)
            task_prompt = task_prompt.replace('{{ _global_instructions }}', global_text)

        return {
            "system": system_prompt.strip(),
            "task": task_prompt.strip(),
        }

    def call_llm(
        self,
        system_prompt: str,
        user_prompt: str = "",
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Call OpenAI API and measure latency.

        Mimics how Pipecat sends messages to OpenAI.
        """
        # Build messages (OpenAI API format)
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (for multi-turn)
        if conversation_history:
            messages.extend(conversation_history)

        # Add user prompt if provided
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})

        # Measure latency with streaming
        start_time = time.time()

        stream = self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            stream=True,
        )

        # Track time to first token
        first_chunk_time = None
        response_text = ""

        for chunk in stream:
            if first_chunk_time is None and chunk.choices[0].delta.content:
                first_chunk_time = time.time()

            if chunk.choices[0].delta.content:
                response_text += chunk.choices[0].delta.content

        end_time = time.time()

        # Calculate metrics
        latency_ms = (first_chunk_time - start_time) * 1000 if first_chunk_time else 0
        total_time_ms = (end_time - start_time) * 1000

        return {
            "response": response_text.strip(),
            "latency_ms": round(latency_ms, 2),
            "total_time_ms": round(total_time_ms, 2),
            "model": self.model,
        }

    def evaluate_single(
        self,
        state: str,
        patient_data: Dict[str, Any],
        test_scenario: Dict[str, Any],
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate a single test case following production workflow.

        Workflow:
        1. Format patient data (DataFormatter)
        2. Render prompts (PromptRenderer)
        3. Call LLM (OpenAI)
        4. Measure latency
        """
        # Step 1: Format patient data (same as ConversationContext)
        formatted_data = self.format_patient_data(patient_data)

        # Step 2: Render prompts (same as production)
        rendered = self.render_prompt_sections(state, formatted_data)

        # Step 3: Build conversation history with user utterance
        history = conversation_history or []
        if test_scenario.get("user_utterance"):
            history = list(history)
            history.append({
                "role": "user",
                "content": test_scenario["user_utterance"]
            })

        # Step 4: Call LLM
        llm_result = self.call_llm(
            system_prompt=rendered["system"],
            user_prompt=rendered.get("task", ""),
            conversation_history=history,
        )

        # Compile result
        result = {
            "timestamp": datetime.now().isoformat(),
            "state": state,
            "scenario_id": test_scenario.get("scenario_id"),
            "scenario_description": test_scenario.get("description"),
            "user_utterance": test_scenario.get("user_utterance"),
            "expected_behavior": test_scenario.get("expected_behavior"),
            "llm_response": llm_result["response"],
            "latency_ms": llm_result["latency_ms"],
            "total_time_ms": llm_result["total_time_ms"],
            "model": llm_result["model"],
            "patient_data": patient_data,
            "conversation_history": history,
            "formatted_data": formatted_data,  # For debugging
            "rendered_prompts": rendered,  # For debugging
        }

        return result

    def evaluate_batch(
        self,
        test_cases: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Evaluate multiple test cases."""
        results = []

        print(f"\nEvaluating {len(test_cases)} test cases...")
        print("=" * 60)

        for i, test_case in enumerate(test_cases, 1):
            scenario_id = test_case['test_scenario'].get('scenario_id', 'unknown')
            print(f"\n[{i}/{len(test_cases)}] {scenario_id}")

            result = self.evaluate_single(
                state=test_case["state"],
                patient_data=test_case["patient_data"],
                test_scenario=test_case["test_scenario"],
                conversation_history=test_case.get("conversation_history"),
            )

            results.append(result)

            # Quick summary
            print(f"  Latency: {result['latency_ms']}ms")
            print(f"  Response: {result['llm_response'][:100]}...")

        return results
