from jinja2 import Environment, BaseLoader, Template
from typing import Dict, Any, Optional
import logging
import time

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
        
        self._template_cache: Dict[str, Template] = {}
        self._precompile_all_templates()  # Always precompile
        
        logger.info("PromptRenderer initialized")
    
    def _precompile_all_templates(self):
        start_time = time.perf_counter()
        compile_count = 0
        
        for state_def in self.schema.states.definitions:
            prompts = self.schema.get_prompts_for_state(state_def.name)
            
            for section_name, template_text in prompts.items():
                if template_text:
                    cache_key = f"{state_def.name}_{section_name}"
                    compiled = self.env.from_string(template_text)
                    self._template_cache[cache_key] = compiled
                    compile_count += 1
        
        compile_time_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"Pre-compiled {compile_count} templates in {compile_time_ms:.2f}ms")
    
    def render_state_prompt(
        self,
        state_name: str,
        precomputed_data: Dict[str, Any],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        context = self._build_context(state_name, precomputed_data, additional_context)
        state = self.schema.get_state(state_name)
        
        sections = []
        prompts = self.schema.get_prompts_for_state(state_name)
        
        for section_name in ['system', 'task']:
            if section_name in prompts:
                cache_key = f"{state_name}_{section_name}"
                template = self._template_cache.get(cache_key)
                if template:
                    rendered = template.render(**context)
                    if rendered.strip():
                        sections.append(rendered.strip())
        
        return "\n\n".join(sections)
    
    def _build_context(
        self,
        state_name: str,
        precomputed_data: Dict[str, Any],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        state = self.schema.get_state(state_name)
        
        context = {
            'voice_name': self.schema.voice.persona.name,
            'voice_role': self.schema.voice.persona.role,
            'voice_company': self.schema.voice.persona.company,
        }
        
        # Add accessible data fields
        for field in state.data_access:
            if field in precomputed_data:
                context[field] = precomputed_data[field]
            
            spoken_field = f"{field}_spoken"
            if spoken_field in precomputed_data:
                context[spoken_field] = precomputed_data[spoken_field]
        
        # Add all precomputed fields as safety net
        for key, value in precomputed_data.items():
            if key not in context:
                context[key] = value
        
        if additional_context:
            context.update(additional_context)
        
        return context