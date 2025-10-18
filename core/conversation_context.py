import logging
from typing import Dict, Any
from .data_formatter import DataFormatter
from .prompt_renderer import PromptRenderer

logger = logging.getLogger(__name__)


class ConversationContext:
    def __init__(
        self, 
        schema, 
        patient_data: Dict[str, Any], 
        session_id: str,
        prompt_renderer: PromptRenderer,
        data_formatter: DataFormatter
    ):
        self.schema = schema
        self.session_id = session_id
        self.current_state = schema.get_initial_state()
        
        self.data_formatter = data_formatter
        self.prompt_renderer = prompt_renderer
        
        # Format patient data once
        self.patient_data = self.data_formatter.format_patient_data(patient_data)
    
    def transition_to(self, new_state: str, reason: str = ""):
        """Transition to a new conversation state."""
        old_state = self.current_state
        self.schema.get_state(new_state)  # Validate state exists
        self.current_state = new_state
        logger.info(f"[{self.session_id}] {old_state} → {new_state}" + (f" ({reason})" if reason else ""))
    
    def render_prompt(self) -> str:
        """Render the prompt for the current state."""
        return self.prompt_renderer.render_state_prompt(self.current_state, self.patient_data)
    
    def get_current_state_definition(self):
        """Get the schema definition for the current state."""
        return self.schema.get_state(self.current_state)