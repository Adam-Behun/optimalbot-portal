"""
Manages conversation state transitions based on schema rules and LLM directives.
"""

from typing import Optional, List, Dict, Any
import re
import logging
from pipecat.frames.frames import LLMMessagesUpdateFrame, EndFrame

logger = logging.getLogger(__name__)


class StateManager:
    
    def __init__(
        self, 
        conversation_context,
        schema,
        session_id: str,
        patient_id: str,
        context_aggregators=None,
        task=None
    ):

        self.conversation_context = conversation_context
        self.schema = schema
        self.session_id = session_id
        self.patient_id = patient_id
        self.context_aggregators = context_aggregators
        self.task = task
    
    def set_task(self, task):
        """Set pipeline task after creation."""
        self.task = task
    
    def set_context_aggregators(self, context_aggregators):
        """Set context aggregators after pipeline creation."""
        self.context_aggregators = context_aggregators
    
    async def check_user_transition(self, user_message: str):
        """Check if user message triggers a state transition based on schema rules."""
        current_state = self.conversation_context.current_state
        transition = self.schema.check_transition(current_state, user_message)
        
        if transition:
            logger.info(
                f"🎯 Transition: {transition.from_state} → {transition.to_state} "
                f"({transition.trigger.type})"
            )
            await self.transition_to(transition.to_state, transition.reason)
    
    async def check_assistant_transition(self, assistant_message: str):
        """Parse assistant response for LLM-directed state transitions."""
        # Extract <next_state> tag
        match = re.search(
            r'<next_state>(\w+)</next_state>', 
            assistant_message, 
            re.IGNORECASE
        )
        if not match:
            return
        
        requested_state = match.group(1).lower()
        current_state = self.conversation_context.current_state
        
        if not self.schema.is_llm_directed(current_state):
            return
        
        allowed_transitions = self.schema.get_allowed_transitions(current_state)
        
        if requested_state in allowed_transitions:
            logger.info(f"🤖 LLM transition: {current_state} → {requested_state}")
            await self.transition_to(requested_state, "llm_directed")
            
            from monitoring import emit_event
            emit_event(
                session_id=self.session_id,
                category="STATE",
                event="llm_directed_transition",
                metadata={
                    "from_state": current_state,
                    "to_state": requested_state
                }
            )
        else:
            logger.warning(
                f"⚠️ LLM transition blocked: {requested_state} "
                f"not in {allowed_transitions}"
            )
            
            from monitoring import emit_event
            emit_event(
                session_id=self.session_id,
                category="STATE",
                event="llm_transition_blocked",
                severity="warning",
                metadata={
                    "from_state": current_state,
                    "requested_state": requested_state,
                    "allowed_transitions": allowed_transitions
                }
            )
    
    async def check_completion(self, transcripts: List[Dict[str, Any]]):
        """Terminate pipeline if in closing state and goodbye said."""
        if self.conversation_context.current_state != "closing":
            return
        
        assistant_messages = [t for t in transcripts if t["role"] == "assistant"]
        if not assistant_messages:
            return
        
        last_msg = assistant_messages[-1]["content"].lower()
        goodbye_phrases = ["goodbye", "have a great day", "thank you"]
        
        if any(phrase in last_msg for phrase in goodbye_phrases):
            logger.info("👋 Call complete - terminating")
            
            from services.patient_db import get_async_patient_db
            await get_async_patient_db().update_call_status(
                self.patient_id, 
                "Completed"
            )
            
            from monitoring import emit_event
            emit_event(
                session_id=self.session_id,
                category="CALL",
                event="call_completed",
                metadata={"patient_id": self.patient_id}
            )
            
            if self.task:
                await self.task.queue_frames([EndFrame()])
    
    async def transition_to(self, new_state: str, reason: str):
        """Perform state transition and update LLM context."""
        if not self.task:
            logger.error(
                f"Cannot transition: task not available "
                f"({self.conversation_context.current_state} → {new_state})"
            )
            return
        
        old_state = self.conversation_context.current_state
        logger.info(f"🔄 {old_state} → {new_state} ({reason})")
        
        # Special states that don't need LLM context update
        if new_state in ["connection", "voicemail_detected", "ivr_stuck"]:
            self.conversation_context.transition_to(new_state, reason=reason)
            return
        
        self.conversation_context.transition_to(new_state, reason=reason)
        new_prompt = self.conversation_context.render_prompt()
        
        # Add patient_id hint for verification state
        if new_state == "verification":
            new_prompt += (
                f"\n\nIMPORTANT: The patient_id for function calls is: "
                f"{self.patient_id}"
            )
        
        # Preserve non-system messages
        current_context = self.context_aggregators.user().context
        current_messages = current_context.messages if current_context else []
        
        new_messages = [{"role": "system", "content": new_prompt}]
        new_messages.extend([
            msg for msg in current_messages 
            if msg.get("role") != "system"
        ])
        
        await self.task.queue_frames([
            LLMMessagesUpdateFrame(messages=new_messages, run_llm=False)
        ])
        
        logger.info(f"✅ Transitioned to {new_state}")