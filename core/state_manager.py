import logging
import re
from typing import Optional, List, Dict, Any
from pipecat.frames.frames import LLMMessagesUpdateFrame, EndFrame
from backend.models import get_async_patient_db
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
        self.task = task

    def set_context_aggregators(self, context_aggregators):
        self.context_aggregators = context_aggregators

    async def check_assistant_transition(self, assistant_message: str):
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
        else:
            logger.warning(
                f"⚠️ LLM transition blocked: {requested_state} "
                f"not in {allowed_transitions}"
            )

    async def check_completion(self, transcripts: List[Dict[str, Any]]):
        if self.conversation_context.current_state != "closing":
            return

        assistant_messages = [t for t in transcripts if t["role"] == "assistant"]
        if not assistant_messages:
            return

        last_msg = assistant_messages[-1]["content"].lower()
        goodbye_phrases = ["goodbye", "have a great day", "thank you"]

        if any(phrase in last_msg for phrase in goodbye_phrases):
            logger.info("👋 Call complete - terminating")

            await get_async_patient_db().update_call_status(
                self.patient_id,
                "Completed"
            )

            if self.task:
                await self.task.queue_frames([EndFrame()])

    async def transition_to(self, new_state: str, reason: str):
        if not self.task:
            logger.error(
                f"Cannot transition: task not available "
                f"({self.conversation_context.current_state} → {new_state})"
            )
            return

        old_state = self.conversation_context.current_state
        logger.info(f"🔄 {old_state} → {new_state} ({reason})")

        if new_state in ["connection", "ivr_stuck"]:
            self.conversation_context.transition_to(new_state, reason=reason)
            return

        self.conversation_context.transition_to(new_state, reason=reason)
        new_prompt = self.conversation_context.render_prompt()

        if new_state == "verification":
            new_prompt += (
                f"\n\nIMPORTANT: The patient_id for function calls is: "
                f"{self.patient_id}"
            )

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