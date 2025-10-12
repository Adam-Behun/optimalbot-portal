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


class StatesConfig(BaseModel):
    initial_state: str
    definitions: List[StateDefinition]


class TransitionTrigger(BaseModel):
    type: str  # e.g., "keyword_detection", "function_call", "timeout"
    keywords: Optional[List[str]] = None
    match_mode: str = "any"  # "any" or "all"


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
        
        # Load schema.yaml
        with open(base_path / 'schema.yaml', 'r') as f:
            schema_data = yaml.safe_load(f)
        
        # Load prompts.yaml
        with open(base_path / 'prompts.yaml', 'r') as f:
            prompts = yaml.safe_load(f)
        
        schema = cls(base_path=base_path, prompts=prompts, **schema_data)
        
        load_time_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"✓ Schema loaded in {load_time_ms:.1f}ms")
        logger.info(f"  - States: {len(schema.states.definitions)}")
        logger.info(f"  - Transitions: {len(schema.transitions)}")
        
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