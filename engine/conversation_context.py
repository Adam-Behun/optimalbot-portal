"""
Conversation Context - Manages per-call state and formatted data.
Lightweight wrapper created for each call with pre-computed data and rendering.
"""

import logging
from typing import Dict, Any
from .schema_loader import ConversationSchema
from .data_formatter import DataFormatter
from .prompt_renderer import PromptRenderer

logger = logging.getLogger(__name__)


class ConversationContext:
    """
    Per-call context that holds formatted patient data and current state.
    Created once per call, minimal overhead.
    """
    
    # Class-level renderer cache to avoid re-compiling templates
    _renderer_cache: Dict[str, PromptRenderer] = {}
    
    def __init__(
        self, 
        schema: ConversationSchema,
        patient_data: Dict[str, Any],
        session_id: str
    ):
        """
        Initialize conversation context.
        
        Args:
            schema: Pre-loaded conversation schema (shared across calls)
            patient_data: Raw patient data from database
            session_id: Unique session identifier
        """
        self.schema = schema
        self.session_id = session_id
        self.current_state = schema.get_initial_state()
        
        # Format patient data once at initialization
        formatter = DataFormatter(schema)
        self.patient_data = formatter.format_patient_data(patient_data)
        
        # Get or create cached prompt renderer (avoids re-compiling templates)
        schema_id = f"{schema.conversation.client_id}_{schema.conversation.version}"
        if schema_id not in self._renderer_cache:
            self._renderer_cache[schema_id] = PromptRenderer(schema)
            logger.info(f"Created cached PromptRenderer for {schema_id}")
        self.renderer = self._renderer_cache[schema_id]
        
        # Track state history
        self.state_history = [self.current_state]
        
        # Context flags for template rendering
        self.returning_from_hold = False
        
        logger.info(
            f"ConversationContext initialized - Session: {session_id}, "
            f"Initial state: {self.current_state}"
        )
    
    def transition_to(self, new_state: str, reason: str = ""):
        """
        Transition to a new state.
        
        Args:
            new_state: Name of the state to transition to
            reason: Optional reason for transition (for logging)
        """
        old_state = self.current_state
        
        # Validate state exists
        try:
            self.schema.get_state(new_state)
        except ValueError as e:
            logger.error(f"Invalid state transition: {e}")
            raise
        
        self.current_state = new_state
        self.state_history.append(new_state)
        
        logger.info(
            f"State transition: {old_state} → {new_state} "
            f"(Session: {self.session_id})" + 
            (f" - Reason: {reason}" if reason else "")
        )
    
    def render_prompt(self) -> str:
        """
        Render the complete prompt for the current state.
        Uses pre-compiled templates and pre-formatted data for minimal latency.
        
        Returns:
            Fully rendered prompt string ready for LLM
        """
        # Build additional context (flags, state-specific data)
        additional_context = {
            'returning_from_hold': self.returning_from_hold
        }
        
        # Render using cached renderer
        return self.renderer.render_state_prompt(
            self.current_state,
            self.patient_data,
            additional_context
        )
    
    def get_current_state_definition(self):
        """Get full state definition for current state."""
        return self.schema.get_state(self.current_state)
    
    def get_streaming_config(self) -> Dict[str, Any]:
        """Get streaming configuration for current state."""
        return self.renderer.get_streaming_config(self.current_state)
    
    def mark_returning_from_hold(self):
        """Mark that we're returning from hold state."""
        self.returning_from_hold = True
    
    def clear_hold_flag(self):
        """Clear the returning from hold flag."""
        self.returning_from_hold = False