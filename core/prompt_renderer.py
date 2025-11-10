import logging
import time
from jinja2 import Environment, BaseLoader, Template
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class PromptRenderer:
    def __init__(self, schema):
        self.schema = schema
        self.env = Environment(
            loader=BaseLoader(),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True
        )
        self._cache: Dict[str, Template] = {}
        self._global_instructions_template: Optional[Template] = None
        self._precompile_all()

    def _precompile_all(self):
        start_time = time.perf_counter()

        prompts_dict = self.schema.prompts if hasattr(self.schema, 'prompts') else {}

        if '_global_instructions' in prompts_dict:
            self._global_instructions_template = self.env.from_string(
                prompts_dict['_global_instructions']
            )

        for state_def in self.schema.states.definitions:
            prompts = self.schema.get_prompts_for_state(state_def.name)
            for section, text in prompts.items():
                if text:
                    key = f"{state_def.name}.{section}"
                    self._cache[key] = self.env.from_string(text)

        if isinstance(prompts_dict, dict):
            for prompt_name, config in prompts_dict.get("prompts", {}).items():
                if isinstance(config, dict):
                    for field, text in config.items():
                        if text and isinstance(text, str):
                            key = f"{prompt_name}.{field}"
                            self._cache[key] = self.env.from_string(text)

        compile_time_ms = (time.perf_counter() - start_time) * 1000
        if compile_time_ms > 5.0:
            logger.debug(f"Prompts compiled ({compile_time_ms:.1f}ms, {len(self._cache)} prompts)")

    def render_state_prompt(self, state_name: str, data: Dict[str, Any]) -> str:
        # Build full context for global instructions (unrestricted access)
        full_context = self._build_simple_context(data)

        global_text = ""
        if self._global_instructions_template:
            global_text = self._global_instructions_template.render(**full_context)

        # Build restricted context for state-specific prompts
        context = self._build_context(state_name, data)
        context['_global_instructions'] = global_text

        sections = []
        for section in ['system', 'task']:
            key = f"{state_name}.{section}"
            if key in self._cache:
                rendered = self._cache[key].render(**context)
                if rendered.strip():
                    sections.append(rendered.strip())

        return "\n\n".join(sections)

    def render_prompt(self, prompt_name: str, field: str, data: Dict[str, Any]) -> str:
        key = f"{prompt_name}.{field}"
        if key not in self._cache:
            return ""

        context = self._build_simple_context(data)

        if self._global_instructions_template:
            global_text = self._global_instructions_template.render(**context)
            context['_global_instructions'] = global_text

        rendered = self._cache[key].render(**context)

        return rendered

    def _build_context(self, state_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        state = self.schema.get_state(state_name)

        context = {
            'voice_name': self.schema.voice.persona.name,
            'voice_role': self.schema.voice.persona.role,
            'client_company': self.schema.voice.persona.client_company,
        }

        for field in state.data_access:
            if field in data:
                context[field] = data[field]
            spoken_field = f"{field}_spoken"
            if spoken_field in data:
                context[spoken_field] = data[spoken_field]

        return context

    def _build_simple_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'voice_name': self.schema.voice.persona.name,
            'voice_role': self.schema.voice.persona.role,
            'client_company': self.schema.voice.persona.client_company,
            **data
        }
