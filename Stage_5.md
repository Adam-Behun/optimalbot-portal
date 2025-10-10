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

STAGE 5: Lightweight State Machine + Memory-Efficient Design
Context for LLM Expert
You are building a state machine that reuses the cached schema (NO re-parsing) and maintains minimal per-call state. This enables 100+ concurrent calls without memory issues.
Files You Need to Request
Please provide:
1. engine/schema_loader.py (to see cached schema)
2. engine/intent_classifier.py from Stage 4
3. Current transition_handlers.py or state management code
4. How you currently track conversation state
Implementation Requirements
5.1 Lightweight State Machine
File: engine/state_machine.py
python"""
State Machine - Lightweight per-call state, shares schema cache.
"""

from typing import Dict, Any, Optional, List
import logging
import time

logger = logging.getLogger(__name__)


class ConversationState:
    """
    Lightweight per-call state.
    References shared schema, doesn't copy it.
    """
    
    __slots__ = [
        'schema',           # Reference to shared schema (not copied)
        'current_state',
        'state_history',
        'intent_classifier', # Reference to shared classifier
        '_created_at'
    ]
    
    def __init__(self, schema, intent_classifier):
        """
        Create lightweight state for one conversation.
        
        Args:
            schema: Reference to shared ConversationSchema (NOT copied)
            intent_classifier: Reference to shared IntentClassifier
        """
        self.schema = schema  # Just a reference, no copying
        self.intent_classifier = intent_classifier
        self.current_state = schema.get_initial_state()
        self.state_history = [self.current_state]
        self._created_at = time.time()
    
    async def process_user_message(
        self, 
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Process user message and determine next state.
        
        Args:
            message: What user said
            context: Optional conversation context
            
        Returns:
            Next state name
        """
        start_time = time.perf_counter()
        
        # Classify intent (fast LLM or keyword fallback)
        intent, classification_ms = await self.intent_classifier.classify(
            message, 
            context
        )
        
        # Get current state definition (just dict lookup)
        current_state_def = self.schema.get_state(self.current_state)
        
        # Find matching transition
        next_state = self.current_state  # Default: stay in current state
        
        for transition in current_state_def.transitions:
            if transition.trigger == intent:
                next_state = transition.next_state
                break
        
        # Transition if needed
        if next_state != self.current_state:
            self.transition_to(next_state)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        
        logger.debug(
            f"State processing: {self.current_state} + '{intent}' -> {next_state} "
            f"in {total_ms:.2f}ms"
        )
        
        return next_state
    
    def transition_to(self, state_name: str) -> None:
        """Transition to new state."""
        if state_name != self.current_state:
            logger.info(f"State transition: {self.current_state} -> {state_name}")
            self.state_history.append(state_name)
            self.current_state = state_name
    
    def get_previous_state(self) -> str:
        """Get previous state (for resuming from hold)"""
        if len(self.state_history) >= 2:
            return self.state_history[-2]
        return self.current_state


class StateManagerFactory:
    """
    Factory that creates lightweight state machines.
    Shares schema and classifier across all calls.
    """
    
    def __init__(self, schema, intent_classifier):
        """
        Initialize factory with shared resources.
        
        Args:
            schema: Shared ConversationSchema instance
            intent_classifier: Shared IntentClassifier instance
        """
        self.schema = schema
        self.intent_classifier = intent_classifier
        
        # Track active conversations
        self._active_states: Dict[str, ConversationState] = {}
        
        logger.info("StateManagerFactory initialized with shared schema")
    
    def create_state(self, conversation_id: str) -> ConversationState:
        """
        Create new lightweight state for a conversation.
        
        Args:
            conversation_id: Unique ID for this conversation
            
        Returns:
            New ConversationState instance
        """
        state = ConversationState(self.schema, self.intent_classifier)
        self._active_states[conversation_id] = state
        
        logger.debug(
            f"Created state for conversation {conversation_id}. "
            f"Active: {len(self._active_states)}"
        )
        
        return state
    
    def get_state(self, conversation_id: str) -> Optional[ConversationState]:
        """Get existing state for a conversation"""
        return self._active_states.get(conversation_id)
    
    def remove_state(self, conversation_id: str) -> None:
        """Clean up state when conversation ends"""
        if conversation_id in self._active_states:
            del self._active_states[conversation_id]
            logger.debug(
                f"Removed state for {conversation_id}. "
                f"Active: {len(self._active_states)}"
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get factory statistics"""
        return {
            "active_conversations": len(self._active_states),
            "schema_version": self.schema.conversation.version,
            "shared_classifier": True
        }
Success Criteria

 Can create 100+ concurrent state machines without memory issues
 State creation <5ms
 State transition <10ms
 Memory usage doesn't grow linearly with conversations

What to Ask the Engineer
Before implementing, please provide:
1. Schema loader and classifier from previous stages
2. How you currently track conversation state per call
3. Your conversation ID generation approach
4. Memory/performance monitoring tools you use

I need to integrate efficient state management into your call flow.