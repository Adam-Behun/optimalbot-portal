# Loads and parses YAML files into Python objects (Pydantic models)
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml
from pydantic import BaseModel
import logging
import time

logger = logging.getLogger(__name__)


class ConversationMetadata(BaseModel):
    name: str
    version: str
    client_id: str


class VoicePersona(BaseModel):
    name: str
    role: str
    company: str


class SpeakingStyle(BaseModel):
    tone: str
    pace: str
    max_words_per_response: int


class VoiceConfig(BaseModel):
    persona: VoicePersona
    speaking_style: SpeakingStyle


class PreformatRule(BaseModel):
    format: str
    grouping: Optional[List[int]] = None


class DataSchema(BaseModel):
    entity_name: str
    required_fields: List[str]
    preformat_rules: Dict[str, PreformatRule] = {}


class StateDefinition(BaseModel):
    name: str
    description: str
    prompts_ref: str
    data_access: List[str] = []
    allowed_transitions: List[str] = []  # NEW
    llm_directed: bool = False  # NEW
    entry_point: bool = False  # Add this if not present
    terminal: bool = False  # Add this if not present
    functions: List[str] = []  # Add this if not present
    required_before_closing: bool = False  # Add this if not present


class StatesConfig(BaseModel):
    initial_state: str
    definitions: List[StateDefinition]


class TransitionTrigger(BaseModel):
    type: str  # e.g., "keyword_detection", "auto", "function_call", "timeout"
    keywords: Optional[List[str]] = None
    match_mode: str = "any"  # "any" or "all"
    delay: float = 0.0 


class TransitionRule(BaseModel):
    from_state: str
    to_state: str
    trigger: TransitionTrigger
    reason: str
    description: Optional[str] = None


class ConversationSchema(BaseModel):
    conversation: ConversationMetadata
    voice: VoiceConfig
    data_schema: DataSchema
    states: StatesConfig
    transitions: List[TransitionRule] = []
    
    base_path: Path
    prompts: Dict[str, Any] = {}
    
    class Config:
        arbitrary_types_allowed = True
    
    @classmethod
    def load(cls, schema_path: str) -> 'ConversationSchema':
        start_time = time.perf_counter()
        base_path = Path(schema_path)
        
        with open(base_path / 'schema.yaml', 'r') as f:
            schema_data = yaml.safe_load(f)
        
        with open(base_path / 'prompts.yaml', 'r') as f:
            prompts = yaml.safe_load(f)
        
        schema = cls(base_path=base_path, prompts=prompts, **schema_data)
        
        load_time_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"Schema loaded ({load_time_ms:.1f}ms)")
        
        return schema
    
    def get_state(self, state_name: str) -> StateDefinition:
        """Get a state definition by name"""
        for state in self.states.definitions:
            if state.name == state_name:
                return state
        raise ValueError(f"State '{state_name}' not found")
    
    def get_initial_state(self) -> str:
        """Get the initial state name"""
        return self.states.initial_state
    
    def get_prompts_for_state(self, state_name: str) -> Dict[str, str]:
        """Get prompts for a given state"""
        state = self.get_state(state_name)
        return self.prompts['prompts'][state.prompts_ref]
    
    def get_transitions_from_state(self, state_name: str) -> List[TransitionRule]:
        """Get all possible transitions from a given state"""
        return [t for t in self.transitions if t.from_state == state_name]
    
    def check_transition(self, current_state: str, user_message: str) -> Optional[TransitionRule]:
        """
        Check if user message triggers any transition from current state.
        
        Args:
            current_state: The current conversation state
            user_message: The user's message to check
            
        Returns:
            TransitionRule if a transition should occur, None otherwise
        """
        # Get all possible transitions from current state
        possible_transitions = self.get_transitions_from_state(current_state)
        
        message_lower = user_message.lower()

        for transition in possible_transitions:
            if transition.trigger.type == "auto":
                return transition
        
        for transition in possible_transitions:
            if transition.trigger.type == "keyword_detection":
                keywords = transition.trigger.keywords or []
                
                if transition.trigger.match_mode == "any":
                    # Any keyword matches
                    if any(keyword.lower() in message_lower for keyword in keywords):
                        return transition
                
                elif transition.trigger.match_mode == "all":
                    # All keywords must match
                    if all(keyword.lower() in message_lower for keyword in keywords):
                        return transition
        
        return None

    def get_allowed_transitions(self, from_state: str) -> list:
        """
        Get list of allowed target states from the given state.
        Returns empty list if state not found or has no allowed transitions.
        """
        state_def = next(
            (s for s in self.states.definitions if s.name == from_state),
            None
        )
        
        if not state_def:
            logger.warning(f"State definition not found: {from_state}")
            return []
        
        return state_def.allowed_transitions

    def is_llm_directed(self, state_name: str) -> bool:
        """
        Check if the given state uses LLM-directed transitions.
        Returns False if state not found or llm_directed not specified.
        """
        state_def = next(
            (s for s in self.states.definitions if s.name == state_name),
            None
        )
        
        if not state_def:
            return False
        
        return state_def.llm_directed