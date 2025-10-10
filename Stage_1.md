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


STAGE 1: Schema Foundation + Pre-computation Infrastructure
Context for LLM Expert
You are implementing the foundational schema system for a voice AI agent. Your goal is to create YAML-based configuration that loads ONCE at startup and pre-computes all expensive operations. This stage adds zero latency to live calls because everything happens during initialization.
Files You Need to Request
Please provide:
1. Current app.py (to understand startup sequence)
2. Any existing patient data models (models.py or similar)
3. Current prompt templates if they exist (to understand what we're replacing)
4. Example patient data structure (to design pre-computation)
Implementation Requirements
1.1 Create Schema with Pre-computation Support
File: clients/prior_auth/schema.yaml
yaml# Conversation metadata
conversation:
  name: "Healthcare Prior Authorization"
  version: "2.0.0"
  client_id: "prior_auth"
  
  # NEW: Pre-computation hints
  precompute_strategy:
    pronunciation_formatters: true  # Pre-format common data at call init
    static_prompts: true            # Render non-dynamic prompts once
    template_cache: true             # Cache Jinja2 templates

# Voice configuration
voice:
  persona:
    name: "Alexandra"
    role: "Medical office assistant"
    company: "Adam's Medical Practice"
  
  speaking_style:
    tone: "professional, friendly"
    pace: "moderate"
    max_words_per_response: 30

# Data schema with formatting hints
data_schema:
  entity_name: "Patient"
  
  required_fields:
    - patient_name
    - date_of_birth
    - insurance_member_id
    - insurance_company_name
    - cpt_code
    - provider_npi
  
  # NEW: Pre-formatting rules (applied once at call start)
  preformat_rules:
    date_of_birth:
      format: "natural_speech"
      # Output: "January fifteenth, nineteen eighty"
    
    insurance_member_id:
      format: "nato_alphabet"
      # Output: "Alpha Bravo Charlie one two three"
    
    cpt_code:
      format: "individual_digits"
      # Output: "9 9 2 1 3"
    
    provider_npi:
      format: "grouped_digits"
      grouping: [3, 3, 4]  # "123 456 7890"

# States remain similar but with streaming hints
states:
  initial_state: "greeting"
  
  definitions:
    - name: "greeting"
      description: "Initial contact with insurance representative"
      respond_immediately: false
      prompts_ref: "greeting"
      
      # NEW: Streaming behavior
      streaming:
        enabled: true
        chunk_size: 50  # words
      
      transitions:
        - trigger: "rep_asked_for_patient_info"
          next_state: "patient_verification"
        - trigger: "rep_said_hold"
          next_state: "on_hold"
    
    - name: "patient_verification"
      description: "Provide patient information as requested"
      respond_immediately: false
      prompts_ref: "patient_verification"
      
      # Data already pre-formatted, just reference it
      data_access:
        - patient_name
        - date_of_birth_spoken  # Pre-formatted version
        - insurance_member_id_spoken
        - cpt_code_spoken
        - provider_npi_spoken
      
      streaming:
        enabled: true
      
      transitions:
        - trigger: "rep_asked_for_authorization"
          next_state: "authorization_check"
        - trigger: "all_info_provided"
          next_state: "authorization_check"
    
    - name: "authorization_check"
      description: "Obtain authorization status"
      respond_immediately: false
      prompts_ref: "authorization_check"
      
      streaming:
        enabled: true
      
      functions:
        - name: "update_prior_auth_status"
          module: "functions"
          required_params:
            - status
            - reference_number
      
      transitions:
        - trigger: "database_updated"
          next_state: "closing"
    
    - name: "closing"
      description: "Thank representative and end call"
      respond_immediately: true
      prompts_ref: "closing"
      
      streaming:
        enabled: true
      
      post_actions:
        - type: "end_conversation"

# NEW: Enhanced intent classification
intent_classification:
  # Use LLM-based classification with few-shot examples
  method: "llm_few_shot"  # or "keyword_hybrid" for faster but less accurate
  
  # Fallback to keywords if LLM too slow
  max_classification_latency_ms: 100
  
  # Few-shot examples for LLM classifier
  examples:
    - user_message: "Can I get the patient's full name?"
      intent: "rep_asked_for_patient_info"
      reasoning: "Rep explicitly requesting patient information"
    
    - user_message: "Hold on, let me pull that up"
      intent: "rep_said_hold"
      reasoning: "Rep indicating they need to check something"
    
    - user_message: "One moment please"
      intent: "rep_said_hold"
      reasoning: "Rep asking caller to wait"
    
    - user_message: "Okay, that's approved, reference number is X123"
      intent: "authorization_provided"
      reasoning: "Rep providing authorization decision and reference"

# Intents - used as fallback or for keyword boosting
intents:
  - name: "rep_asked_for_patient_info"
    description: "Rep asks for patient information"
    keywords:
      - "patient name"
      - "date of birth"
      - "member id"
      - "birthday"
    patterns:
      - "can i get"
      - "what is the"
      - "i need the"
  
  - name: "rep_said_hold"
    description: "Rep putting us on hold"
    keywords:
      - "hold"
      - "one moment"
      - "give me a second"
    patterns:
      - "let me check"
      - "i'll be right back"

# NEW: Minimal observability for v1
observability:
  track_latency:
    schema_load_time: true
    precompute_time: true
    state_transition_time: true
    prompt_render_time: true
  
  # Log only essential events
  events:
    - state_transitions
    - intent_classifications
    - function_calls
    - latency_warnings  # Log if any operation >50ms
1.2 Create Schema Loader with Caching
File: engine/schema_loader.py
python"""
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
    format: str  # "natural_speech", "nato_alphabet", etc.
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
    transitions: List[StateTransition]
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
Success Criteria

 Schema loads in <500ms at startup
 Validation catches missing fields, invalid transitions
 Load time logged and tracked
 Tests pass: pytest tests/test_schema_loader.py -v

What to Ask the Engineer
Before implementing, please provide:
1. Current application startup code (app.py)
2. Existing patient data models
3. Any current prompt/template systems
4. Example of actual patient data structure

This helps me design pre-computation to match your data flow.