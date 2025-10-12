import logging
from typing import Dict, Any
from .data_formatter import DataFormatter
from .prompt_renderer import PromptRenderer

logger = logging.getLogger(__name__)


class ConversationContext:
    _renderer_cache: Dict[str, PromptRenderer] = {}
    
    def __init__(self, schema, patient_data: Dict[str, Any], session_id: str):
        self.schema = schema
        self.session_id = session_id
        self.current_state = schema.get_initial_state()
        
        # Format patient data once
        formatter = DataFormatter(schema)
        self.patient_data = formatter.format_patient_data(patient_data)
        
        # Get or create cached renderer
        schema_id = f"{schema.conversation.client_id}_{schema.conversation.version}"
        if schema_id not in self._renderer_cache:
            self._renderer_cache[schema_id] = PromptRenderer(schema)
        self.renderer = self._renderer_cache[schema_id]
        
        logger.info(f"ConversationContext initialized - Session: {session_id}, State: {self.current_state}")
    
    def transition_to(self, new_state: str, reason: str = ""):
        old_state = self.current_state
        self.schema.get_state(new_state)  # Validate
        self.current_state = new_state
        logger.info(f"[{self.session_id}] {old_state} → {new_state}" + (f" ({reason})" if reason else ""))
    
    def render_prompt(self) -> str:
        return self.renderer.render_state_prompt(self.current_state, self.patient_data)
    
    def get_current_state_definition(self):
        return self.schema.get_state(self.current_state)