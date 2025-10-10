"""
Prompt Renderer - Pre-compiles templates and renders with pre-computed data.
Supports streaming-ready prompt generation with minimal latency.
"""

from jinja2 import Environment, BaseLoader, Template, TemplateSyntaxError
from typing import Dict, Any, Optional
import logging
import time

logger = logging.getLogger(__name__)


class PromptRenderer:
    """
    Renders prompts using pre-computed data with pre-compiled templates.
    Templates are compiled once at initialization for zero parse overhead.
    """
    
    def __init__(self, schema):
        """
        Initialize renderer and pre-compile all templates.
        
        Args:
            schema: ConversationSchema instance
        """
        self.schema = schema
        
        # Jinja2 environment with minimal overhead
        self.env = Environment(
            loader=BaseLoader(),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True
        )
        
        # Template cache: {state_section: compiled_template}
        self._template_cache: Dict[str, Template] = {}
        
        # Pre-compile templates if strategy enabled
        if schema.conversation.precompute_strategy.template_cache:
            self._precompile_all_templates()
        
        logger.info("PromptRenderer initialized")
    
    def _precompile_all_templates(self):
        """
        Pre-compile all Jinja2 templates at startup.
        This eliminates template parsing overhead during calls.
        """
        start_time = time.perf_counter()
        compile_count = 0
        
        logger.info("Pre-compiling prompt templates...")
        
        for state_def in self.schema.states.definitions:
            try:
                prompts = self.schema.get_prompts_for_state(state_def.name)
                
                # Compile each section (system, task, style, etc.)
                for section_name, template_text in prompts.items():
                    if template_text:  # Skip empty sections
                        cache_key = f"{state_def.name}_{section_name}"
                        
                        try:
                            compiled = self.env.from_string(template_text)
                            self._template_cache[cache_key] = compiled
                            compile_count += 1
                            
                        except TemplateSyntaxError as e:
                            logger.error(
                                f"Template syntax error in {cache_key}: {e}"
                            )
                            raise
                            
            except Exception as e:
                logger.error(
                    f"Failed to compile templates for state '{state_def.name}': {e}"
                )
                raise
        
        compile_time_ms = (time.perf_counter() - start_time) * 1000
        
        logger.info(
            f"Pre-compiled {compile_count} templates in {compile_time_ms:.2f}ms"
        )
        
        if compile_time_ms > 100:
            logger.warning(
                f"Template compilation took {compile_time_ms:.2f}ms (target: <100ms)"
            )
    
    def render_state_prompt(
        self,
        state_name: str,
        precomputed_data: Dict[str, Any],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Render complete prompt for a state using pre-computed data.
        
        Args:
            state_name: Name of the state to render
            precomputed_data: Pre-formatted patient data (with _spoken fields)
            additional_context: Optional additional context (e.g., returning_from_hold)
            
        Returns:
            Fully rendered prompt string
        """
        start_time = time.perf_counter()
        
        # Build context dictionary
        context = self._build_context(state_name, precomputed_data, additional_context)
        
        # Get state definition
        state = self.schema.get_state(state_name)
        
        # Render each section
        sections = []
        prompts = self.schema.get_prompts_for_state(state_name)
        
        for section_name in ['system', 'task', 'style']:
            if section_name in prompts:
                cache_key = f"{state_name}_{section_name}"
                
                # Use cached template if available
                if cache_key in self._template_cache:
                    template = self._template_cache[cache_key]
                else:
                    # Fallback: compile on-demand (shouldn't happen if cache enabled)
                    template = self.env.from_string(prompts[section_name])
                    logger.warning(
                        f"Template {cache_key} not in cache, compiling on-demand"
                    )
                
                # Render with context
                try:
                    rendered = template.render(**context)
                    
                    # Only add non-empty sections
                    if rendered.strip():
                        sections.append(rendered.strip())
                        
                except Exception as e:
                    logger.error(
                        f"Error rendering {section_name} for {state_name}: {e}"
                    )
                    raise
        
        # Combine all sections
        full_prompt = "\n\n".join(sections)
        
        # Track performance
        render_time_ms = (time.perf_counter() - start_time) * 1000
        
        # Log performance
        logger.debug(
            f"Rendered prompt for '{state_name}' in {render_time_ms:.2f}ms"
        )
        
        if render_time_ms > 10:
            logger.warning(
                f"Prompt render for '{state_name}' took {render_time_ms:.2f}ms "
                f"(target: <10ms)"
            )
        
        return full_prompt
    
    def _build_context(
        self,
        state_name: str,
        precomputed_data: Dict[str, Any],
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build template context from pre-computed data and schema.
        This is fast because all formatting is already done.
        
        Args:
            state_name: Current state name
            precomputed_data: Pre-formatted patient data
            additional_context: Optional additional variables
            
        Returns:
            Complete context dictionary for template rendering
        """
        # Get state definition
        state = self.schema.get_state(state_name)
        
        # Start with voice configuration (static, can be cached)
        context = {
            'voice_name': self.schema.voice.persona.name,
            'voice_role': self.schema.voice.persona.role,
            'voice_company': self.schema.voice.persona.company,
            'tone': self.schema.voice.speaking_style.tone,
            'pace': self.schema.voice.speaking_style.pace,
            'max_words': self.schema.voice.speaking_style.max_words_per_response,
        }
        
        # Add accessible data fields (no formatting needed - already done)
        for field in state.data_access:
            if field in precomputed_data:
                context[field] = precomputed_data[field]
            
            # Also add _spoken version if it exists
            spoken_field = f"{field}_spoken"
            if spoken_field in precomputed_data:
                context[spoken_field] = precomputed_data[spoken_field]
        
        # Add any fields not in data_access but referenced in templates
        # This is a safety net - prompts might reference fields not listed
        for key, value in precomputed_data.items():
            if key not in context:
                context[key] = value
        
        # Merge additional context (flags like returning_from_hold)
        if additional_context:
            context.update(additional_context)
        
        return context
    
    def get_streaming_config(self, state_name: str) -> Dict[str, Any]:
        """
        Get streaming configuration for a state.
        
        Args:
            state_name: State to check
            
        Returns:
            Streaming config dict or default
        """
        state = self.schema.get_state(state_name)
        
        if state.streaming:
            return {
                'enabled': state.streaming.enabled,
                'chunk_size': state.streaming.chunk_size
            }
        
        # Default: streaming enabled
        return {'enabled': True, 'chunk_size': 50}