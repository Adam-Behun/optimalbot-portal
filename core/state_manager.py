import logging
import re
from typing import Optional, List, Dict, Any
from pipecat.frames.frames import LLMMessagesUpdateFrame, EndFrame, ManuallySwitchServiceFrame
from backend.models import get_async_patient_db
from backend.functions import PATIENT_TOOLS
logger = logging.getLogger(__name__)

class StateManager:

    def __init__(
        self,
        conversation_context,
        schema,
        session_id: str,
        patient_id: str,
        context_aggregators=None,
        task=None,
        main_llm=None,
        classifier_llm=None
    ):
        self.conversation_context = conversation_context
        self.schema = schema
        self.session_id = session_id
        self.patient_id = patient_id
        self.context_aggregators = context_aggregators
        self.task = task
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.monitor = None

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

        # Special handling for end_call pseudo-state (graceful shutdown)
        if requested_state == "end_call":
            logger.info("🔚 LLM requested call termination via end_call")
            await get_async_patient_db().update_call_status(self.patient_id, "Completed")
            if self.task:
                await self.task.queue_frames([EndFrame()])
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

    async def transition_to(self, new_state: str, reason: str):
        if not self.task:
            logger.error(
                f"Cannot transition: task not available "
                f"({self.conversation_context.current_state} → {new_state})"
            )
            return

        old_state = self.conversation_context.current_state

        if old_state == new_state:
            logger.debug(f"Already in {new_state}, skipping transition (reason: {reason})")
            return

        logger.info(f"🔄 {old_state} → {new_state} ({reason})")

        if new_state in ["connection", "ivr_stuck"]:
            self.conversation_context.transition_to(new_state, reason=reason)
            return

        self.conversation_context.transition_to(new_state, reason=reason)

        # Switch LLMs based on state transition
        if new_state in ["verification", "closing"]:
            # Switch to main_llm and enable function calling
            if self.main_llm:
                switch_frame = ManuallySwitchServiceFrame(service=self.main_llm)
                await self.task.queue_frames([switch_frame])
                logger.debug(f"Switched to main_llm for {new_state} state")

            # Enable tools for function calling
            context = self.context_aggregators.user().context
            context.set_tools(PATIENT_TOOLS)
            logger.debug(f"Enabled function calling tools for {new_state}")

        elif new_state == "greeting":
            if self.monitor:
                self.monitor.reset()

            if self.classifier_llm:
                switch_frame = ManuallySwitchServiceFrame(service=self.classifier_llm)
                await self.task.queue_frames([switch_frame])
                logger.debug("Switched to classifier_llm for greeting state")

            context = self.context_aggregators.user().context
            context.set_tools([])
            logger.debug("Cleared tools for greeting state")

        new_prompt = self.conversation_context.render_prompt()

        if new_state in ["verification", "closing"]:
            new_prompt += (
                f"\n\nIMPORTANT: The patient_id for function calls is: "
                f"{self.patient_id}"
            )

        current_context = self.context_aggregators.user().context
        current_messages = current_context.messages if current_context else []

        new_messages = [{"role": "system", "content": new_prompt}]

        # Determine whether to run LLM immediately after transition
        if new_state == "greeting":
            if reason == "ivr_complete":
                logger.info("🧹 Clearing all IVR navigation context - fresh greeting prompt only")
                run_llm = False
            elif reason == "human_answered":
                logger.debug("Preserving conversation - human answered, preparing to respond")
                new_messages.extend([
                    msg for msg in current_messages
                    if msg.get("role") != "system"
                ])
                run_llm = False
                logger.debug("Greeting state ready - waiting for human to finish speaking before generating response")
            else:
                # Preserve existing conversation messages
                new_messages.extend([
                    msg for msg in current_messages
                    if msg.get("role") != "system"
                ])
                # If there are user messages in the context, run LLM to respond to them
                has_user_messages = any(msg.get("role") == "user" for msg in new_messages)
                run_llm = has_user_messages
                if run_llm:
                    logger.debug("Running LLM immediately - user messages detected in greeting state")
                else:
                    logger.debug("Not running LLM - no user messages yet")
        elif new_state in ["verification", "closing"]:
            # When transitioning TO verification/closing FROM greeting via LLM,
            # we need to run the LLM immediately with the new verification prompt
            # to generate the first verification response
            new_messages.extend([
                msg for msg in current_messages
                if msg.get("role") != "system"
            ])
            run_llm = True
            logger.debug(f"Will run LLM immediately after transition to {new_state}")
        else:
            new_messages.extend([
                msg for msg in current_messages
                if msg.get("role") != "system"
            ])
            run_llm = False

        await self.task.queue_frames([
            LLMMessagesUpdateFrame(messages=new_messages, run_llm=run_llm)
        ])

        logger.info(f"✅ Transitioned to {new_state}")