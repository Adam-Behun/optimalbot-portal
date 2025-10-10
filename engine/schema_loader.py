"""
Schema Loader - Loads once, caches everything, minimal runtime overhead.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml
from pydantic import BaseModel, Field, validator
import logging
import time

logger = logging.getLogger(__name__)


class PrecomputeStrategy(BaseModel):
    """Strategy for pre-computation"""
    pronunciation_formatters: bool = True
    static_prompts: bool = True
    template_cache: bool = True


class ConversationMetadata(BaseModel):
    """Conversation metadata"""
    name: str
    version: str
    client_id: str
    precompute_strategy: PrecomputeStrategy


class VoicePersona(BaseModel):
    """Voice persona configuration"""
    name: str
    role: str
    company: str


class SpeakingStyle(BaseModel):
    """Speaking style configuration"""
    tone: str
    pace: str
    max_words_per_response: int


class VoiceConfig(BaseModel):
    """Voice configuration"""
    persona: VoicePersona
    speaking_style: SpeakingStyle


class PreformatRule(BaseModel):
    """Pre-formatting rule for data field"""
    format: str  # "natural_speech", "spell_out", etc.
    grouping: Optional[List[int]] = None


class DataSchema(BaseModel):
    """Data schema definition"""
    entity_name: str
    required_fields: List[str]
    optional_fields: List[str] = []
    output_fields: List[str] = []
    preformat_rules: Dict[str, PreformatRule] = {}


class StreamingConfig(BaseModel):
    """Streaming configuration for state"""
    enabled: bool = True
    chunk_size: int = 50  # words


class StateTransition(BaseModel):
    """State transition definition"""
    trigger: str
    next_state: str


class StateDefinition(BaseModel):
    """Individual state definition"""
    name: str
    description: str
    respond_immediately: bool = True
    prompts_ref: str
    data_access: List[str] = []
    streaming: Optional[StreamingConfig] = None
    functions: List[Dict[str, Any]] = []
    transitions: List[StateTransition] = []
    post_actions: List[Dict[str, Any]] = []


class StatesConfig(BaseModel):
    """States configuration"""
    initial_state: str
    definitions: List[StateDefinition]


class IntentExample(BaseModel):
    """Few-shot example for intent classification"""
    user_message: str
    intent: str
    reasoning: str


class IntentClassificationConfig(BaseModel):
    """Intent classification configuration"""
    method: str = "llm_few_shot"  # or "keyword_hybrid"
    max_classification_latency_ms: int = 100
    examples: List[IntentExample] = []


class IntentDefinition(BaseModel):
    """Intent classification definition (fallback/keyword)"""
    name: str
    description: str
    keywords: List[str] = []
    patterns: List[str] = []


class ObservabilityConfig(BaseModel):
    """Observability configuration"""
    track_latency: Dict[str, bool]
    events: List[str]


class ConversationSchema(BaseModel):
    """
    Complete conversation schema with caching and pre-computation.
    """
    
    # Metadata
    conversation: ConversationMetadata
    
    # Configuration
    voice: VoiceConfig
    data_schema: DataSchema
    states: StatesConfig
    intent_classification: IntentClassificationConfig
    intents: List[IntentDefinition]
    observability: ObservabilityConfig
    
    # Runtime data (set after loading)
    base_path: Path
    prompts: Dict[str, Any] = {}
    
    # NEW: Performance tracking
    _load_time_ms: float = 0.0
    
    class Config:
        arbitrary_types_allowed = True
    
    @validator('states')
    def validate_state_transitions(cls, states, values):
        """Validate that all state transitions reference existing states"""
        state_names = {s.name for s in states.definitions}
        
        for state in states.definitions:
            for transition in state.transitions:
                if transition.next_state not in state_names:
                    raise ValueError(
                        f"State '{state.name}' has transition to "
                        f"non-existent state '{transition.next_state}'"
                    )
        
        return states
    
    @classmethod
    def load(cls, schema_path: str) -> 'ConversationSchema':
        """
        Load schema once, measure time, cache everything.
        """
        start_time = time.perf_counter()
        
        base_path = Path(schema_path)
        
        if not base_path.exists():
            raise FileNotFoundError(f"Schema directory not found: {schema_path}")
        
        # Load main schema
        schema_file = base_path / 'schema.yaml'
        if not schema_file.exists():
            raise FileNotFoundError(f"schema.yaml not found in {schema_path}")
        
        logger.info(f"Loading schema from {schema_file}")
        with open(schema_file, 'r') as f:
            schema_data = yaml.safe_load(f)
        
        # Load prompts
        prompts_file = base_path / 'prompts.yaml'
        if not prompts_file.exists():
            raise FileNotFoundError(f"prompts.yaml not found in {schema_path}")
        
        logger.info(f"Loading prompts from {prompts_file}")
        with open(prompts_file, 'r') as f:
            prompts = yaml.safe_load(f)
        
        # Create schema instance
        try:
            schema = cls(
                base_path=base_path,
                prompts=prompts,
                **schema_data
            )
            
            # Track load time
            load_time_ms = (time.perf_counter() - start_time) * 1000
            schema._load_time_ms = load_time_ms
            
            logger.info(
                f"✓ Schema loaded: {schema.conversation.name} "
                f"v{schema.conversation.version} in {load_time_ms:.2f}ms"
            )
            
            # Warn if slow
            if load_time_ms > 500:
                logger.warning(f"Schema load took {load_time_ms:.2f}ms (target: <500ms)")
            
            return schema
            
        except Exception as e:
            logger.error(f"Schema validation failed: {e}")
            raise ValueError(f"Invalid schema: {e}")
    
    def get_state(self, state_name: str) -> StateDefinition:
        """Get state definition by name."""
        for state in self.states.definitions:
            if state.name == state_name:
                return state
        raise ValueError(f"State '{state_name}' not found in schema")
    
    def get_initial_state(self) -> str:
        """Get the name of the initial state"""
        return self.states.initial_state
    
    def get_prompts_for_state(self, state_name: str) -> Dict[str, str]:
        """Get prompts for a specific state."""
        state = self.get_state(state_name)
        prompts_ref = state.prompts_ref
        
        if prompts_ref not in self.prompts.get('prompts', {}):
            raise ValueError(f"Prompts reference '{prompts_ref}' not found")
        
        return self.prompts['prompts'][prompts_ref]