Technical Refactoring Plan: Universal Voice Engine + Pluggable Conversation Schemas
Version: 2.0 - Optimized for Production Voice AI
Critical Design Principles (Applied Throughout)
Latency-First Design

Pre-compute everything possible at initialization: Format pronunciations, render static prompts, cache templates
Minimize runtime overhead: Target <50ms total schema overhead per conversation turn
Benchmark everything: Every stage must measure and report latency impact

Streaming-Native Architecture

Schema components must support incremental prompt building
State transitions don't block response streaming
LLM can start responding before full prompt assembly

Smart State Management

Schema parsed once at app startup, cached in memory
Per-call state machines are lightweight (no schema re-parsing)
Minimal object creation in hot path

STAGE 2: Pre-computation Engine + Streaming-Ready Renderer
Context for LLM Expert
You are building a renderer that pre-computes expensive operations (date formatting, NATO alphabet) at call initialization, NOT during conversation flow. This eliminates 20-50ms per response. You're also designing for streaming: prompts must be buildable incrementally.
Files You Need to Request
Please provide:
1. The schema_loader.py from Stage 1
2. clients/prior_auth/prompts.yaml
3. Example patient data dictionary (actual structure)
4. Current pipeline.py (to understand where data formatting happens)
Implementation Requirements
2.1 Pre-computation Formatter (Runs Once Per Call)
File: engine/precompute.py
python"""
Pre-computation Engine - Format all data once at call initialization.
Zero overhead during conversation.
"""

from datetime import datetime
from typing import Dict, Any
import logging
import time

logger = logging.getLogger(__name__)


class DataPrecomputer:
    """
    Pre-formats all patient data according to schema rules.
    Runs ONCE when call starts, not during conversation.
    """
    
    # NATO alphabet lookup (class variable, loaded once)
    NATO_MAP = {
        'A': 'Alpha', 'B': 'Bravo', 'C': 'Charlie', 'D': 'Delta',
        'E': 'Echo', 'F': 'Foxtrot', 'G': 'Golf', 'H': 'Hotel',
        'I': 'India', 'J': 'Juliet', 'K': 'Kilo', 'L': 'Lima',
        'M': 'Mike', 'N': 'November', 'O': 'Oscar', 'P': 'Papa',
        'Q': 'Quebec', 'R': 'Romeo', 'S': 'Sierra', 'T': 'Tango',
        'U': 'Uniform', 'V': 'Victor', 'W': 'Whiskey', 'X': 'X-ray',
        'Y': 'Yankee', 'Z': 'Zulu'
    }
    
    # Ordinal number words
    ORDINALS = {
        1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
        6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
        11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
        15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
        19: "nineteenth", 20: "twentieth", 21: "twenty-first", 22: "twenty-second",
        23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth",
        26: "twenty-sixth", 27: "twenty-seventh", 28: "twenty-eighth",
        29: "twenty-ninth", 30: "thirtieth", 31: "thirty-first"
    }
    
    def __init__(self, schema):
        """
        Initialize with schema.
        
        Args:
            schema: ConversationSchema instance
        """
        self.schema = schema
        self.preformat_rules = schema.data_schema.preformat_rules
    
    def precompute_all(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Pre-compute all formatted versions of patient data.
        This runs ONCE at call start.
        
        Args:
            patient_data: Raw patient data
            
        Returns:
            Enhanced data with "_spoken" versions of formatted fields
        """
        start_time = time.perf_counter()
        
        # Copy original data
        enhanced_data = dict(patient_data)
        
        # Apply each pre-format rule
        for field_name, rule in self.preformat_rules.items():
            if field_name not in patient_data:
                continue
            
            raw_value = patient_data[field_name]
            spoken_key = f"{field_name}_spoken"
            
            # Format based on rule
            if rule.format == "natural_speech":
                enhanced_data[spoken_key] = self._format_date_natural(raw_value)
            
            elif rule.format == "nato_alphabet":
                enhanced_data[spoken_key] = self._format_nato(raw_value)
            
            elif rule.format == "individual_digits":
                enhanced_data[spoken_key] = self._format_individual_digits(raw_value)
            
            elif rule.format == "grouped_digits":
                enhanced_data[spoken_key] = self._format_grouped_digits(
                    raw_value, 
                    rule.grouping or [3, 3, 4]
                )
        
        # Track performance
        compute_time_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"Pre-computed data formatting in {compute_time_ms:.2f}ms")
        
        if compute_time_ms > 50:
            logger.warning(
                f"Pre-computation took {compute_time_ms:.2f}ms (target: <50ms)"
            )
        
        return enhanced_data
    
    def _format_date_natural(self, date_string: str) -> str:
        """Convert date to natural speech."""
        try:
            date_obj = datetime.strptime(date_string, "%Y-%m-%d")
            month = date_obj.strftime("%B")
            day = self.ORDINALS.get(date_obj.day, f"{date_obj.day}th")
            year = self._year_to_speech(date_obj.year)
            return f"{month} {day}, {year}"
        except Exception as e:
            logger.warning(f"Failed to format date '{date_string}': {e}")
            return date_string
    
    def _format_nato(self, text: str) -> str:
        """Convert to NATO phonetic alphabet."""
        result = []
        for char in str(text).upper():
            if char.isalpha():
                result.append(self.NATO_MAP.get(char, char))
            elif char.isdigit():
                result.append(char)
            elif char not in [' ', '-', '_']:
                result.append(char)
        return " ".join(result)
    
    def _format_individual_digits(self, text: str) -> str:
        """Separate all digits with spaces."""
        return " ".join(str(text))
    
    def _format_grouped_digits(self, text: str, grouping: List[int]) -> str:
        """
        Group digits for easier speaking.
        Example: "1234567890" with grouping [3,3,4] -> "123 456 7890"
        """
        text_str = str(text)
        groups = []
        start = 0
        
        for size in grouping:
            if start >= len(text_str):
                break
            groups.append(text_str[start:start+size])
            start += size
        
        # Add remainder if any
        if start < len(text_str):
            groups.append(text_str[start:])
        
        return " ".join(groups)
    
    def _year_to_speech(self, year: int) -> str:
        """Convert year to spoken format."""
        if year >= 2000 and year < 2010:
            return f"two thousand {year - 2000}" if year > 2000 else "two thousand"
        elif year >= 2010:
            decade = (year // 10) % 10
            unit = year % 10
            tens_names = ["", "ten", "twenty", "thirty", "forty", 
                         "fifty", "sixty", "seventy", "eighty", "ninety"]
            if unit == 0:
                return f"twenty {tens_names[decade]}"
            else:
                return f"twenty {tens_names[decade]}-{unit}"
        else:
            # Pre-2000
            first_two = year // 100
            last_two = year % 100
            if last_two == 0:
                return f"{first_two} hundred"
            return f"{first_two} {last_two}"
2.2 Streaming-Ready Prompt Renderer
File: engine/prompt_renderer.py
python"""
Prompt Renderer - Uses pre-computed data, supports streaming.
"""

from jinja2 import Environment, BaseLoader, Template
from typing import Dict, Any, List
import logging
import time

logger = logging.getLogger(__name__)


class PromptRenderer:
    """
    Renders prompts using pre-computed data.
    Supports incremental/streaming rendering.
    """
    
    def __init__(self, schema):
        """
        Initialize with schema and pre-compile all templates.
        
        Args:
            schema: ConversationSchema instance
        """
        self.schema = schema
        
        # Jinja2 environment
        self.env = Environment(
            loader=BaseLoader(),
            autoescape=False
        )
        
        # NEW: Pre-compile all templates at init
        self._template_cache: Dict[str, Template] = {}
        if schema.conversation.precompute_strategy.template_cache:
            self._precompile_templates()
    
    def _precompile_templates(self):
        """Pre-compile all Jinja2 templates at startup."""
        logger.info("Pre-compiling prompt templates...")
        
        for state_def in self.schema.states.definitions:
            prompts = self.schema.get_prompts_for_state(state_def.name)
            
            for section_name in ['identity', 'task', 'style']:
                if section_name in prompts:
                    cache_key = f"{state_def.name}_{section_name}"
                    template = self.env.from_string(prompts[section_name])
                    self._template_cache[cache_key] = template
        
        logger.info(f"Pre-compiled {len(self._template_cache)} templates")
    
    def render_state_prompt(
        self, 
        state_name: str, 
        precomputed_data: Dict[str, Any],
        streaming: bool = False
    ) -> str:
        """
        Render prompt for a state using pre-computed data.
        
        Args:
            state_name: Name of the state
            precomputed_data: Data with "_spoken" fields already computed
            streaming: If True, return prompt optimized for streaming
            
        Returns:
            Rendered prompt string
        """
        start_time = time.perf_counter()
        
        # Build context (fast - just dict assembly)
        context = self._build_context(state_name, precomputed_data)
        
        # Render each section using cached templates
        sections = []
        
        for section_name in ['identity', 'task', 'style']:
            cache_key = f"{state_name}_{section_name}"
            
            if cache_key in self._template_cache:
                template = self._template_cache[cache_key]
                rendered = template.render(**context)
                sections.append(f"## {section_name.upper()}\n{rendered}")
        
        # Combine sections
        full_prompt = "\n\n".join(sections)
        
        # Track performance
        render_time_ms = (time.perf_counter() - start_time) * 1000
        
        if render_time_ms > 10:  # Should be <10ms with pre-compiled templates
            logger.warning(
                f"Prompt render took {render_time_ms:.2f}ms for {state_name}"
            )
        
        return full_prompt
    
    def _build_context(
        self, 
        state_name: str, 
        precomputed_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build context dictionary from pre-computed data.
        This is fast because all formatting is already done.
        """
        # Start with voice config
        context = {
            'voice': {
                'persona': {
                    'name': self.schema.voice.persona.name,
                    'role': self.schema.voice.persona.role,
                    'company': self.schema.voice.persona.company,
                },
                'speaking_style': {
                    'tone': self.schema.voice.speaking_style.tone,
                    'pace': self.schema.voice.speaking_style.pace,
                    'max_words_per_response': 
                        self.schema.voice.speaking_style.max_words_per_response,
                }
            }
        }
        
        # Get state config
        state = self.schema.get_state(state_name)
        
        # Add accessible data (just dict lookup, no formatting)
        for field in state.data_access:
            if field in precomputed_data:
                context[field] = precomputed_data[field]
        
        return context
Success Criteria

 Pre-computation completes in <50ms per call
 Prompt rendering <10ms per state (cached templates)
 Tests show formatted data matches expected output
 Latency tracked and logged

What to Ask the Engineer
Before implementing, please provide:
1. Schema loader from Stage 1
2. Actual patient data example (with all fields)
3. Current prompts.yaml
4. Where in pipeline.py call initialization happens

I need to hook pre-computation into call start, not conversation loop.