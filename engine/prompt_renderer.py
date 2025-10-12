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
        self._global_instructions_template: Optional[Template] = None  # ✅ NEW
        self._precompile_all_templates()
        
        logger.info("PromptRenderer initialized")
    
    def _precompile_all_templates(self):
        start_time = time.perf_counter()
        compile_count = 0
        
        # ✅ NEW: Precompile global instructions if they exist
        prompts_dict = self.schema.prompts if hasattr(self.schema, 'prompts') else {}
        if isinstance(prompts_dict, dict) and '_global_instructions' in prompts_dict:
            global_text = prompts_dict['_global_instructions']
            self._global_instructions_template = self.env.from_string(global_text)
            compile_count += 1
            logger.info("Pre-compiled global instructions template")
        
        # Precompile state-specific templates
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
        """Render state-specific prompt with global instructions injected"""
        context = self._build_context(state_name, precomputed_data, additional_context)
        state = self.schema.get_state(state_name)
        
        # ✅ NEW: Render global instructions first (if they exist)
        global_instructions_text = ""
        if self._global_instructions_template:
            global_instructions_text = self._global_instructions_template.render(**context)
        
        sections = []
        prompts = self.schema.get_prompts_for_state(state_name)
        
        for section_name in ['system', 'task']:
            if section_name in prompts:
                cache_key = f"{state_name}_{section_name}"
                template = self._template_cache.get(cache_key)
                if template:
                    rendered = template.render(**context)
                    
                    # ✅ NEW: Replace placeholder with rendered global instructions
                    if '{{ _global_instructions }}' in rendered:
                        rendered = rendered.replace(
                            '{{ _global_instructions }}',
                            global_instructions_text
                        )
                    
                    if rendered.strip():
                        sections.append(rendered.strip())
        
        return "\n\n".join(sections)
    
    def _build_context(
        self,
        state_name: str,
        precomputed_data: Dict[str, Any],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Build template context with voice config and accessible data"""
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