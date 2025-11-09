import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ConversationMetadata(BaseModel):
    name: str
    version: str
    client_id: str


class VoicePersona(BaseModel):
    name: str
    role: str
    client_company: str


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
    speech_overrides: Dict[str, str] = {}


class StateDefinition(BaseModel):
    name: str
    description: str
    prompts_ref: str
    data_access: List[str] = []
    allowed_transitions: List[str] = []
    llm_directed: bool = False
    entry_point: bool = False
    terminal: bool = False
    functions: List[str] = []
    required_before_closing: bool = False


class StatesConfig(BaseModel):
    initial_state: str
    definitions: List[StateDefinition]


class TransitionTrigger(BaseModel):
    type: str
    keywords: Optional[List[str]] = None
    match_mode: str = "any"
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

    def get_state(self, state_name: str) -> StateDefinition:
        for state in self.states.definitions:
            if state.name == state_name:
                return state
        raise ValueError(f"State '{state_name}' not found")

    def get_initial_state(self) -> str:
        return self.states.initial_state

    def get_prompts_for_state(self, state_name: str) -> Dict[str, str]:
        state = self.get_state(state_name)
        return self.prompts['prompts'][state.prompts_ref]

    def get_transitions_from_state(self, state_name: str) -> List[TransitionRule]:
        return [t for t in self.transitions if t.from_state == state_name]

    def get_allowed_transitions(self, from_state: str) -> List[str]:
        state_def = next(
            (s for s in self.states.definitions if s.name == from_state),
            None
        )

        if not state_def:
            logger.warning(f"State not found: {from_state}")
            return []

        return state_def.allowed_transitions

    def is_llm_directed(self, state_name: str) -> bool:
        state_def = next(
            (s for s in self.states.definitions if s.name == state_name),
            None
        )

        return state_def.llm_directed if state_def else False
