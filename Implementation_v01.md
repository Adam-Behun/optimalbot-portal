# Implementation v01 - Greeting State with LLM Switching

## **Overview**

This document outlines the complete implementation for adding a new "greeting" state to the voice AI system with intelligent LLM switching between classifier_llm and main_llm.

---

## **Desired Behavior Summary**

### **Call Flow:**
```
Call Start → call_classifier (classifier_llm)
  ↓
[IVR] → ivr_navigation (main_llm) → Human Reached → greeting (classifier_llm)
  ↓
[Human] → greeting (classifier_llm)
  ↓
Greeting mentions patient name → verification (main_llm with tools)
  ↓
Transfer detected → call_classifier (classifier_llm) → [CYCLE REPEATS]
```

### **Key Changes:**
1. **call_classifier** state is initial state (managed by IVRNavigator)
2. **greeting** state uses classifier_llm, has only patient_name, says name then transitions
3. **verification** state can transition back to call_classifier on transfer
4. Clear conversation history when entering greeting (delete IVR history)
5. LLM switching happens automatically based on state
6. Wait for human, but if silent, proactively greet

---

## **CODE CHANGES**

### **1. clients/prior_auth/schema.yaml**

**Current:** initial_state is "verification", no greeting state, no call_classifier explicit state

**New:** Add call_classifier as initial state, add greeting state, update transitions

```yaml
# clients/prior_auth/schema.yaml

# State machine
states:
  initial_state: "call_classifier"  # ✅ CHANGED from "verification"

  definitions:
    # ✅ NEW STATE - Call classifier (IVRNavigator handles this)
    - name: "call_classifier"
      description: "Classify incoming audio as IVR or human conversation"
      prompts_ref: "call_classifier"
      allowed_transitions: ["ivr_navigation", "greeting"]
      llm_directed: false  # IVRNavigator handles automatically
      data_access: []
      functions: []

    # Event-driven states - DO NOT MODIFY BEHAVIOR
    - name: "ivr_navigation"
      description: "Navigating IVR system to reach human representative"
      prompts_ref: "ivr_navigation"
      allowed_transitions: ["greeting"]  # ✅ CHANGED from [] to ["greeting"]
      llm_directed: false
      data_access: []  # IVR navigation doesn't need patient data

    - name: "ivr_stuck"
      description: "IVR navigation failed - unable to reach human"
      prompts_ref: "ivr_stuck"
      terminal: true
      allowed_transitions: []
      llm_directed: false
      data_access: []

    # ✅ NEW STATE - Greeting
    - name: "greeting"
      description: "Natural greeting with human, mention patient name, confirm readiness"
      prompts_ref: "greeting"
      data_access:
        - patient_name  # ✅ ONLY patient name available
      functions: []  # No function calling in greeting
      allowed_transitions: ["verification"]  # Can only go to verification
      llm_directed: true  # LLM decides when ready via <next_state>

    # Conversation states - ENABLE LLM DIRECTION
    - name: "verification"
      description: "Provide patient information and verify insurance coverage"
      prompts_ref: "verification"
      data_access:
        - patient_name
        - date_of_birth
        - insurance_member_id
        - cpt_code
        - provider_npi
        - patient_id
        - provider_name
        - facility
        - insurance_company
        - appointment_time
      functions:
        - update_prior_auth_status
        - dial_supervisor
      allowed_transitions: ["closing", "call_classifier"]  # ✅ ADDED call_classifier
      llm_directed: true
      required_before_closing: true

    - name: "closing"
      description: "Close the call politely"
      prompts_ref: "closing"
      terminal: true
      allowed_transitions: []
      llm_directed: false
      data_access:
        - patient_id
        - patient_name
        - date_of_birth
        - facility
        - insurance_company
        - insurance_member_id
        - insurance_phone
        - cpt_code
        - provider_npi
        - provider_name
        - appointment_time

# Transition rules
transitions:
  # ============================================================================
  # EVENT-DRIVEN TRANSITIONS (IVR/Human Classification)
  # ============================================================================

  # ✅ NEW: call_classifier → ivr_navigation (IVRNavigator detects IVR)
  - from_state: "call_classifier"
    to_state: "ivr_navigation"
    trigger:
      type: "event"
      event_name: "on_ivr_status_changed:DETECTED"
    reason: "ivr_detected"
    description: "IVR system detected, begin navigation"

  # ✅ NEW: call_classifier → greeting (IVRNavigator detects human)
  - from_state: "call_classifier"
    to_state: "greeting"
    trigger:
      type: "event"
      event_name: "on_conversation_detected"
    reason: "human_detected"
    description: "Human detected, enter greeting state"

  # ✅ MODIFIED: ivr_navigation → greeting (was verification)
  - from_state: "ivr_navigation"
    to_state: "greeting"
    trigger:
      type: "event"
      event_name: "on_ivr_status_changed:COMPLETED"
    reason: "ivr_navigation_complete"
    description: "Successfully navigated IVR to reach human, transition to greeting"

  # Transition 2: ivr_navigation → ivr_stuck (via event handler - IVR stuck)
  - from_state: "ivr_navigation"
    to_state: "ivr_stuck"
    trigger:
      type: "event"
      event_name: "on_ivr_status_changed:STUCK"
    reason: "ivr_navigation_failed"
    description: "IVR navigation failed - unable to proceed"

  # ✅ NEW: greeting → verification (LLM-directed after mentioning patient name)
  - from_state: "greeting"
    to_state: "verification"
    trigger:
      type: "llm_directed"
      tag: "<next_state>verification</next_state>"
    reason: "greeting_complete"
    description: "Greeting complete, ready for verification"

  # ✅ NEW: verification → call_classifier (transfer detected)
  - from_state: "verification"
    to_state: "call_classifier"
    trigger:
      type: "llm_directed"
      tag: "<next_state>call_classifier</next_state>"
    reason: "transfer_detected"
    description: "Transfer detected, re-classify new connection"
```

---

### **2. clients/prior_auth/prompts.yaml**

**Current:** No greeting prompt

**New:** Add greeting prompt with only patient_name

```yaml
# clients/prior_auth/prompts.yaml

prompts:
  call_classifier:
    system: |
      You are a call classifier. Analyze the transcribed text to classify as IVR or human.

      IVR (respond <mode>ivr</mode>): Menu options like "Press 1", automated prompts like "Enter your account number", scripted intros like "Welcome to [company]", hold messages like "Please hold".

      Human (respond <mode>conversation</mode>): Personal greetings like "Hello, this is [name]", interactive questions like "How can I help?", natural speech with hesitations or direct engagement.

      Respond ONLY with <mode>ivr</mode> or <mode>conversation</mode>.

  _global_instructions: |
    # ... existing global instructions unchanged ...

  ivr_navigation:
    task: |
      # ... existing IVR navigation prompt unchanged ...

  ivr_stuck:
    system: |
      # ... existing ivr_stuck prompt unchanged ...

  # ✅ NEW PROMPT - Greeting state
  greeting:
    system: |
      You are Alexandra from {{ voice_company }}, a medical office assistant.
      You just connected with a human on the phone.

      PATIENT INFORMATION (AVAILABLE IN THIS STATE):
        - Patient Name: {{ patient_name }}

      {{ _global_instructions }}

    task: |
      You are speaking with a human who just answered the phone OR you were just transferred to a new person.

      CONVERSATION FLOW:
      1. WAIT FOR THEIR GREETING (if they speak first)
         - They may say: "Hello", "This is [name]", "How can I help you?", etc.
         - Respond naturally: "Hi! How are you doing?" or "Good morning!"
         - Keep it brief and friendly

      2. IF THEY ARE SILENT (no response after 2-3 seconds)
         - Proactively greet: "Hello, this is Alexandra from Adam's Medical Practice."

      3. STATE YOUR PURPOSE WITH PATIENT NAME
         - Say: "I'm calling regarding a patient named {{ patient_name }}. Can you help me verify their eligibility and benefits?"
         - OR if they ask "How can I help?", say: "I'm calling about a patient named {{ patient_name }}. I need to verify their eligibility and benefits. Can you assist with that?"

      4. DETECT READINESS
         - If they say YES ("Sure", "I can help", "Go ahead", "Yes", etc.) → Include <next_state>verification</next_state>
         - If they need clarification, provide it naturally then transition when they confirm

      BEHAVIORAL RULES:
      - Be natural and conversational (not robotic)
      - Keep individual responses under 20 words
      - Be warm and professional
      - ALWAYS mention the patient's name: {{ patient_name }}
      - Do NOT provide other patient details yet (that's for verification state)
      - Do NOT mention states or tags aloud

      TRANSITION SIGNAL:
      - <next_state>verification</next_state> → When they confirm they can help

      CURRENT STATE: greeting

      Example exchanges:

      Human: "Hello, this is John speaking."
      You: "Hi John! I'm calling about a patient named {{ patient_name }}. Can you help me verify their eligibility? <next_state>verification</next_state>"

      Human: "Good morning, how can I help you?"
      You: "Good morning! I'm calling regarding a patient named {{ patient_name }}. I need to verify their eligibility and benefits. Can you assist? <next_state>verification</next_state>"

      Human: [silence]
      You: "Hello, this is Alexandra from Adam's Medical Practice. I'm calling about a patient named {{ patient_name }}. Can you help me verify their eligibility? <next_state>verification</next_state>"

      Human: "Sure, I can help. What do you need?"
      You: "Great! I need to verify eligibility for {{ patient_name }}. <next_state>verification</next_state>"

  verification:
    system: |
      # ... existing system prompt unchanged ...

    task: |
      You are in a conversation with an insurance representative. YOU are calling THEM to verify coverage.

      WORKFLOW - Follow these steps in order:

      1. PROVIDE PATIENT INFORMATION PROACTIVELY
        - Start by stating: "I have a patient named {{ patient_name }} who will be undergoing a procedure with CPT code {{ cpt_code }}."
        - Then ask: "Can you help me verify their eligibility and benefits?"
        - Wait for them to indicate they can help

      2. RESPOND TO THEIR QUESTIONS
        - They will ask for patient details (DOB, Member ID, etc.)
        - Provide information from PATIENT INFORMATION section when asked
        - Spell out IDs, member numbers clearly
        - Repeat information as many times as needed
        - If asked for information NOT in PATIENT INFORMATION, say: "I don't have that information available"

      3. VERIFY INSURANCE COVERAGE
        - After providing all requested patient information, confirm coverage
        - Listen for their response about whether the procedure is covered

      4. RECORD AUTHORIZATION STATUS
        Use the update_prior_auth_status function based on their response:
        - If APPROVED/AUTHORIZED/COVERED → Call: update_prior_auth_status(patient_id="{{ patient_id }}", status="Approved")
        - If DENIED/NOT COVERED → Call: update_prior_auth_status(patient_id="{{ patient_id }}", status="Denied")
        - If PENDING/UNDER REVIEW → Call: update_prior_auth_status(patient_id="{{ patient_id }}", status="Pending")

      5. GET REFERENCE NUMBER
        - After recording status, ask: "Could you provide a reference or authorization number for this verification?"
        - When they provide it, call: update_prior_auth_status(patient_id="{{ patient_id }}", status="[same_status]", reference_number="[the_number_they_gave]")

      6. SUPERVISOR TRANSFER (USE SPARINGLY)
        - ONLY use dial_supervisor if the representative explicitly requests to speak with a human supervisor
        - Examples: "Can I speak to your manager?", "I need to talk to a real person", "Transfer me to a supervisor"
        - Before transferring, say: "Let me transfer you to a supervisor now."
        - Then call: dial_supervisor()
        - Do NOT offer transfer proactively - only when they request it

      # ✅ NEW STEP 7 - Transfer Detection
      7. TRANSFER DETECTION
        - If the representative says they're transferring you:
          * "Let me transfer you"
          * "I'll connect you to [department]"
          * "Hold on, I'll get someone who can help"
          * "Let me put you through to..."
          * "One moment while I transfer you"
        - Respond naturally: "Sure, I'll hold while you transfer me."
        - Include: <next_state>call_classifier</next_state>
        - This will re-classify the next person (might be IVR or human again)

      CONVERSATION GUIDELINES:
      - YOU called THEM - take initiative to provide information
      - Be natural and conversational, not robotic
      - Keep individual responses under 30 words
      - Stay professional and helpful

      IMPORTANT: The patient_id for all function calls is: {{ patient_id }}

      CURRENT STATE: verification

      You can transition to these states by including the tag at the END of your response:

      <next_state>closing</next_state>
      - Use when: ALL of the following are complete:
        1. ✅ You called update_prior_auth_status with status (Approved/Denied/Pending)
        2. ✅ You called update_prior_auth_status with reference_number
      - Do NOT use if any step is incomplete

      <next_state>call_classifier</next_state>
      - Use when: Being transferred to another person/department
      - Say "I'll hold" then include this tag

      RULES:
      1. Only include the <next_state> tag if criteria are clearly met
      2. If unsure or still gathering information, stay in current state (no tag)
      3. Never mention states or navigation aloud to the caller
      4. Place tag at the very end of your response after all spoken content

  closing:
    # ... existing closing prompt unchanged ...
```

---

### **3. handlers/ivr.py**

**Current:** Hardcoded greeting, direct transition to verification, switch to main_llm

**New:** Transition to greeting state, stay on classifier_llm, clear IVR history

```python
# handlers/ivr.py

import re
from datetime import datetime
from loguru import logger
from pipecat.frames.frames import (
    LLMMessagesUpdateFrame,
    VADParamsUpdateFrame,
    TTSSpeakFrame,
    EndFrame,
    ManuallySwitchServiceFrame
)
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.extensions.ivr.ivr_navigator import IVRStatus
from backend.models import get_async_patient_db
from backend.functions import PATIENT_TOOLS

# ❌ REMOVE: No longer using hardcoded greeting
# HUMAN_GREETING = "Hi, this is Alexandra from Adam's Medical Practice. I'm calling to verify eligibility and benefits for a patient."


def _process_ivr_conversation(conversation_history, pipeline):
    """
    Process IVR conversation history and add to transcript.
    Extracts DTMF selections and logs them as "Pressed X".

    Args:
        conversation_history: List of messages from IVRNavigator
        pipeline: Pipeline object with transcripts list
    """
    if not conversation_history:
        return

    for msg in conversation_history:
        content = msg.get('content', '')
        role = msg.get('role', 'assistant')

        # Extract DTMF tags (e.g., <dtmf>2</dtmf>)
        dtmf_match = re.search(r'<dtmf>(\d+)</dtmf>', content)

        if dtmf_match:
            # Log DTMF selection explicitly
            pipeline.transcripts.append({
                "role": "system",
                "content": f"Pressed {dtmf_match.group(1)}",
                "timestamp": datetime.now().isoformat(),
                "type": "ivr_action"
            })

            # Also add clean content without DTMF tags if any
            clean_content = re.sub(r'<dtmf>\d+</dtmf>', '', content).strip()
            if clean_content:
                pipeline.transcripts.append({
                    "role": role,
                    "content": clean_content,
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr"
                })
        else:
            # Regular IVR message (menu prompts, verbal responses)
            pipeline.transcripts.append({
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "type": "ivr"
            })


def setup_ivr_handlers(pipeline, ivr_navigator):
    """Setup IVRNavigator event handlers for state-based flow"""
    pipeline.ivr_navigator = ivr_navigator

    logger.info(f"🔧 IVR handlers configured for session: {pipeline.session_id}")

    @ivr_navigator.event_handler("on_conversation_detected")
    async def on_conversation_detected(processor, conversation_history):
        """
        Fires when human detected.
        Transition to greeting state (classifier_llm handles natural greeting).
        """
        try:
            logger.info(f"👤 Human detected - Session: {pipeline.session_id}")
            logger.debug(f"Conversation history length: {len(conversation_history) if conversation_history else 0}")

            # Save IVR conversation to transcript (for DB/frontend only)
            _process_ivr_conversation(conversation_history, pipeline)

            # ✅ NEW: Transition to greeting state (classifier_llm handles this)
            # This will:
            # 1. Switch to classifier_llm (from state_manager)
            # 2. Clear conversation history (fresh start)
            # 3. Load greeting prompt with only patient_name
            await pipeline.state_manager.transition_to("greeting", "human_detected")

            # ✅ NEW: Faster VAD for conversation (not IVR menus)
            await pipeline.task.queue_frames([
                VADParamsUpdateFrame(VADParams(stop_secs=0.8))
            ])

            logger.info("✅ Transitioned to greeting state - classifier_llm active")

        except Exception as e:
            logger.error(f"❌ Error in conversation handler: {e}")

    @ivr_navigator.event_handler("on_ivr_status_changed")
    async def on_ivr_status_changed(processor, status):
        """Handle IVR navigation status changes"""
        try:
            if status == IVRStatus.DETECTED:
                logger.info("🤖 IVR system detected - auto-navigation starting")

                # ✅ KEEP: Switch to main LLM for IVR navigation (needs smarter model)
                switch_frame = ManuallySwitchServiceFrame(service=pipeline.main_llm)
                await pipeline.task.queue_frames([switch_frame])

                # ✅ KEEP: Enable tools for main_llm (though IVR shouldn't call functions)
                context = pipeline.context_aggregators.user().context
                context.set_tools(PATIENT_TOOLS)

                logger.info("✅ Switched to main LLM for IVR navigation")

                # Add IVR detection summary to transcript
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "IVR system detected - navigating automatically",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

            elif status == IVRStatus.COMPLETED:
                logger.info("✅ IVR navigation complete - human reached")

                # Add IVR completion summary to transcript
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "Completed",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                # ✅ NEW: Transition to greeting state (classifier_llm handles natural greeting)
                # This will:
                # 1. Switch to classifier_llm (from state_manager)
                # 2. Clear conversation history (fresh start)
                # 3. Load greeting prompt with only patient_name
                await pipeline.state_manager.transition_to("greeting", "ivr_complete")

                # ✅ NEW: Faster VAD for conversation
                await pipeline.task.queue_frames([
                    VADParamsUpdateFrame(VADParams(stop_secs=0.8))
                ])

                logger.info("✅ Transitioned to greeting state - classifier_llm active")

            elif status == IVRStatus.STUCK:
                logger.warning("⚠️ IVR navigation stuck - ending call")

                # Add IVR stuck summary to transcript
                pipeline.transcripts.append({
                    "role": "system",
                    "content": "Failed - navigation stuck",
                    "timestamp": datetime.now().isoformat(),
                    "type": "ivr_summary"
                })

                pipeline.conversation_context.transition_to("ivr_stuck", "navigation_failed")

                await get_async_patient_db().update_call_status(pipeline.patient_id, "Failed")
                await pipeline.task.queue_frames([EndFrame()])

                logger.info("❌ Call ended - IVR stuck")

        except Exception as e:
            logger.error(f"❌ Error in IVR status handler: {e}")
```

---

### **4. core/state_manager.py**

**Current:** Only manages state transitions, no LLM switching, no history clearing

**New:** Add LLM switching, tool management, conversation history clearing

```python
# core/state_manager.py

import logging
import re
from typing import Optional, List, Dict, Any
from pipecat.frames.frames import LLMMessagesUpdateFrame, EndFrame, ManuallySwitchServiceFrame  # ✅ ADD ManuallySwitchServiceFrame
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
        task=None,
        llm_switcher=None,      # ✅ NEW
        classifier_llm=None,    # ✅ NEW
        main_llm=None           # ✅ NEW
    ):
        self.conversation_context = conversation_context
        self.schema = schema
        self.session_id = session_id
        self.patient_id = patient_id
        self.context_aggregators = context_aggregators
        self.task = task
        self.llm_switcher = llm_switcher      # ✅ NEW
        self.classifier_llm = classifier_llm  # ✅ NEW
        self.main_llm = main_llm              # ✅ NEW

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

        # ✅ NEW: State-to-LLM mapping
        STATE_LLM_MAP = {
            "call_classifier": self.classifier_llm,  # Fast classifier, no tools
            "greeting": self.classifier_llm,          # Conversational greeting, no tools
            "ivr_navigation": self.main_llm,          # Smart IVR navigation, with tools
            "verification": self.main_llm,            # Function calling, with tools
            "closing": self.main_llm,                 # Final questions possible, with tools
            "ivr_stuck": None                         # Terminal state, no LLM needed
        }

        # ✅ NEW: Switch LLM if needed
        target_llm = STATE_LLM_MAP.get(new_state)
        if target_llm and hasattr(self, 'llm_switcher') and self.llm_switcher:
            current_active = self.llm_switcher.active_llm
            if current_active != target_llm:
                logger.info(f"🔀 Switching LLM: {type(current_active).__name__} → {type(target_llm).__name__}")
                await self.task.queue_frames([
                    ManuallySwitchServiceFrame(service=target_llm)
                ])

        # ✅ NEW: Manage tools based on state
        context = self.context_aggregators.user().context if self.context_aggregators else None
        if new_state in ["call_classifier", "greeting"]:
            # Classifier states: NO tools
            from openai._types import NOT_GIVEN
            if context:
                context.set_tools(NOT_GIVEN)
            logger.debug(f"🔧 Tools disabled for {new_state}")
        elif new_state in ["ivr_navigation", "verification", "closing"]:
            # Main LLM states: WITH tools
            from backend.functions import PATIENT_TOOLS
            if context:
                context.set_tools(PATIENT_TOOLS)
            logger.debug(f"🔧 Tools enabled for {new_state}")

        # Handle special states (no prompt needed)
        if new_state in ["ivr_stuck"]:
            self.conversation_context.transition_to(new_state, reason=reason)
            return

        # ✅ NEW: Clear conversation history for greeting state (fresh start)
        if new_state == "greeting":
            logger.info("🧹 Clearing conversation history for fresh greeting")
            # Update context state first
            self.conversation_context.transition_to(new_state, reason=reason)
            new_prompt = self.conversation_context.render_prompt()

            # Build fresh messages - ONLY system prompt, NO conversation history
            new_messages = [{"role": "system", "content": new_prompt}]

            await self.task.queue_frames([
                LLMMessagesUpdateFrame(messages=new_messages, run_llm=False)
            ])

            logger.info(f"✅ Transitioned to {new_state} with fresh context")
            return

        # ✅ NEW: Clear conversation history for call_classifier (new call segment)
        if new_state == "call_classifier":
            logger.info("🧹 Clearing conversation history for new call segment")
            # Update context state first
            self.conversation_context.transition_to(new_state, reason=reason)
            new_prompt = self.conversation_context.render_prompt()

            # Build fresh messages - ONLY system prompt, NO conversation history
            new_messages = [{"role": "system", "content": new_prompt}]

            await self.task.queue_frames([
                LLMMessagesUpdateFrame(messages=new_messages, run_llm=False)
            ])

            logger.info(f"✅ Transitioned to {new_state} with fresh context")
            return

        # Regular state transition (keep conversation history)
        self.conversation_context.transition_to(new_state, reason=reason)
        new_prompt = self.conversation_context.render_prompt()

        # Special handling for verification (add patient_id reminder)
        if new_state == "verification":
            new_prompt += (
                f"\n\nIMPORTANT: The patient_id for function calls is: "
                f"{self.patient_id}"
            )

        current_context = self.context_aggregators.user().context if self.context_aggregators else None
        current_messages = current_context.messages if current_context else []

        # Build new messages (keep conversation history, replace system prompt)
        new_messages = [{"role": "system", "content": new_prompt}]
        new_messages.extend([
            msg for msg in current_messages
            if msg.get("role") != "system"
        ])

        await self.task.queue_frames([
            LLMMessagesUpdateFrame(messages=new_messages, run_llm=False)
        ])

        logger.info(f"✅ Transitioned to {new_state}")
```

---

### **5. pipeline/runner.py**

**Current:** Doesn't pass LLM references to state_manager

**New:** Pass llm_switcher, classifier_llm, main_llm to state_manager

```python
# pipeline/runner.py

# ... existing imports and code ...

class ConversationPipeline:

    # ... existing __init__ unchanged ...

    async def run(self, room_url: str, room_token: str, room_name: str):
        logger.info(f"🎬 Starting call - Client: {self.client_name}, Session: {self.session_id}, Phone: {self.phone_number}")

        # Build pipeline
        session_data = {
            'session_id': self.session_id,
            'patient_id': self.patient_id,
            'patient_data': self.patient_data,
            'phone_number': self.phone_number
        }

        room_config = {
            'room_url': room_url,
            'room_token': room_token,
            'room_name': room_name
        }

        logger.debug(f"Session data keys: {list(session_data.keys())}")
        logger.debug(f"Room: {room_name}")

        # Build pipeline (services and components log their own creation)
        self.pipeline, self.transport, components = PipelineFactory.build(
            self.client_config,
            session_data,
            room_config
        )

        # Extract components
        self.conversation_context = components['context']
        self.state_manager = components['state_manager']
        self.transcript_processor = components['transcript_processor']
        self.context_aggregators = components['context_aggregators']
        self.ivr_navigator = components['ivr_navigator']
        self.llm_switcher = components['llm_switcher']
        self.classifier_llm = components['classifier_llm']  # ✅ NEW
        self.main_llm = components['main_llm']

        logger.debug(f"Initial state: {self.conversation_context.current_state}")
        logger.debug(f"Active LLM: {type(self.llm_switcher.active_llm).__name__}")

        # Setup handlers before creating task
        logger.info("🔧 Setting up handlers")
        setup_dialout_handlers(self)
        setup_transcript_handler(self)
        setup_ivr_handlers(self, components['ivr_navigator'])
        setup_function_call_handler(self)

        logger.debug("Creating pipeline task with tracing enabled")
        self.task = PipelineTask(
            self.pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            enable_tracing=True,
            enable_turn_tracking=True,
            conversation_id=self.session_id,
            additional_span_attributes={
                "patient.id": self.patient_id,
                "phone.number": self.phone_number,
                "client.name": self.client_name,
            }
        )

        self.state_manager.set_task(self.task)

        # ✅ NEW: Pass LLM references to state_manager for switching
        self.state_manager.llm_switcher = self.llm_switcher
        self.state_manager.classifier_llm = self.classifier_llm
        self.state_manager.main_llm = self.main_llm

        self.runner = PipelineRunner()

        logger.info(f"🚀 Starting pipeline runner - Initial state: {self.conversation_context.current_state}")

        try:
            await self.runner.run(self.task)
            logger.info("✅ Call completed successfully")

        except Exception as e:
            logger.error(f"❌ Pipeline error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

        finally:
            logger.debug("Cleaning up pipeline resources")
            try:
                if self.task:
                    await self.task.cancel()
                if self.transport:
                    # Daily transport cleanup handled by Pipecat
                    pass
                logger.debug("Cleanup complete")
            except Exception as cleanup_error:
                logger.error(f"Error during cleanup: {cleanup_error}")

    # ... rest of the class unchanged ...
```

---

### **6. pipeline/pipeline_factory.py**

**Current:** Doesn't return classifier_llm in components

**New:** Return classifier_llm in components dictionary

```python
# pipeline/pipeline_factory.py

# ... existing imports and code ...

class PipelineFactory:

    # ... existing build() method unchanged ...

    @staticmethod
    def _create_conversation_components(
        client_config,
        session_data: Dict[str, Any],
        services: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create conversation context, state manager, and handlers"""
        logger.debug("Creating conversation context")
        context = ConversationContext(
            schema=client_config.schema,
            patient_data=session_data['patient_data'],
            session_id=session_data['session_id'],
            prompt_renderer=client_config.prompt_renderer,
            data_formatter=client_config.data_formatter
        )

        logger.debug("Creating state manager")
        state_manager = StateManager(
            conversation_context=context,
            schema=client_config.schema,
            session_id=session_data['session_id'],
            patient_id=session_data['patient_id']
        )

        logger.debug("Creating transcript processor")
        transcript_processor = TranscriptProcessor()

        logger.debug("Setting up LLM context")
        initial_prompt = context.render_prompt()
        llm_context = OpenAILLMContext(
            messages=[{"role": "system", "content": initial_prompt}],
            tools=NOT_GIVEN  # Start with no tools - classifier_llm is active initially
        )
        context_aggregators = services['main_llm'].create_context_aggregator(llm_context)

        # Link state manager to context aggregators
        state_manager.set_context_aggregators(context_aggregators)

        # Get formatted data for prompt rendering (for conversation states only)
        formatted_data = client_config.data_formatter.format_patient_data(
            session_data['patient_data']
        )

        logger.debug("Configuring IVR navigator")
        # Render call classifier prompt from YAML
        call_classifier_prompt = client_config.prompt_renderer.render_prompt(
            "call_classifier", "system", {}
        )

        # Create IVR navigator
        ivr_goal = client_config.prompt_renderer.render_prompt(
            "ivr_navigation", "task", {}  # No patient data needed for IVR navigation
        ) or "Navigate to provider services for eligibility verification"

        # Configure IVRNavigator with LLM switcher
        ivr_navigator = IVRNavigator(
            llm=services['llm_switcher'],  # LLM switcher (classifier active initially)
            ivr_prompt=ivr_goal,
            ivr_vad_params=VADParams(stop_secs=2.0)
        )

        # Override the classifier prompt with our custom one from YAML
        if call_classifier_prompt:
            ivr_navigator._classifier_prompt = call_classifier_prompt
            ivr_navigator._ivr_processor._classifier_prompt = call_classifier_prompt

        logger.debug(f"Components created - Initial state: {context.current_state}")
        return {
            'context': context,
            'state_manager': state_manager,
            'transcript_processor': transcript_processor,
            'context_aggregators': context_aggregators,
            'ivr_navigator': ivr_navigator,
            'llm_switcher': services['llm_switcher'],
            'classifier_llm': services['classifier_llm'],  # ✅ NEW
            'main_llm': services['main_llm']
        }

    # ... existing _assemble_pipeline() method unchanged ...
```

---

## **IMPLEMENTATION SUMMARY**

### **Files Modified:**
1. `clients/prior_auth/schema.yaml` - Added call_classifier, greeting states, updated transitions
2. `clients/prior_auth/prompts.yaml` - Added greeting prompt, updated verification for transfers
3. `handlers/ivr.py` - Removed hardcoded greeting, transition to greeting state
4. `core/state_manager.py` - Added LLM switching, tool management, history clearing
5. `pipeline/runner.py` - Pass LLM references to state_manager
6. `pipeline/pipeline_factory.py` - Return classifier_llm in components

### **Key Features:**
- ✅ Automatic LLM switching based on state
- ✅ Conversation history cleared on greeting/call_classifier states
- ✅ Tools enabled/disabled based on state requirements
- ✅ Transfer detection returns to call_classifier for re-classification
- ✅ Greeting state only has patient_name access
- ✅ Multiple transfer cycles supported

### **State Flow:**
```
call_classifier → [IVR → ivr_navigation] OR [Human → greeting]
greeting → verification
verification → [closing] OR [transfer → call_classifier]
```

### **LLM Usage:**
- **classifier_llm**: call_classifier, greeting (no tools)
- **main_llm**: ivr_navigation, verification, closing (with tools)
