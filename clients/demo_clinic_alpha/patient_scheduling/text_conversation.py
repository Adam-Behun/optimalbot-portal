"""
Text (SMS) conversation handler for patient scheduling.

Minimal implementation to continue voice conversations over SMS.
Uses the same LLM but with text-optimized prompts.
"""

import os
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI


class TextConversation:
    """Handle SMS conversations for patient scheduling."""

    def __init__(
        self,
        patient_id: str,
        organization_id: str,
        organization_name: str = "Demo Clinic Alpha",
        initial_context: Optional[Dict[str, Any]] = None,
    ):
        self.patient_id = patient_id
        self.organization_id = organization_id
        self.organization_name = organization_name
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # State carried over from voice call (appointment details, patient info)
        self.state = initial_context or {}

        # Conversation history for this text session
        self.messages: List[Dict[str, str]] = []

    def _get_system_prompt(self) -> str:
        """System prompt optimized for SMS conversations."""
        state = self.state

        # Build context from voice call
        context_parts = []
        if state.get("first_name"):
            context_parts.append(f"Patient: {state.get('first_name')} {state.get('last_name', '')}")
        if state.get("appointment_date"):
            context_parts.append(f"Appointment: {state.get('appointment_date')} at {state.get('appointment_time')}")
        if state.get("appointment_reason"):
            context_parts.append(f"Visit reason: {state.get('appointment_reason')}")
        if state.get("email"):
            context_parts.append(f"Email: {state.get('email')}")

        context_str = "\n".join(context_parts) if context_parts else "No prior context"

        return f"""You are Monica, a scheduling assistant for {self.organization_name}, continuing a conversation over text message.

# Context from previous call
{context_str}

# SMS Style Guidelines
- Keep responses under 160 characters when possible (1 SMS)
- Be conversational but brief
- No emojis unless the patient uses them first
- Use line breaks sparingly

# What you can help with
- Answer questions about their upcoming appointment
- Help reschedule (collect new preferred date/time, confirm change)
- Provide office information
- Confirm appointment details

# What to redirect
For billing, insurance, medical questions, or urgent issues:
→ "For that, please call our office at [phone] or reply STAFF and we'll have someone reach out."

# Special commands
If patient texts:
- "CANCEL" → Confirm they want to cancel, then mark for staff follow-up
- "RESCHEDULE" → Ask for new preferred date/time
- "STAFF" → Note that a staff member will call them back"""

    def build_context_from_transcript(self, transcript: List[Dict[str, Any]]) -> str:
        """Build a summary from voice call transcript for context."""
        if not transcript:
            return ""

        # Extract key exchanges (last 10 messages max to stay concise)
        recent = transcript[-10:] if len(transcript) > 10 else transcript

        summary_parts = ["Summary of previous call:"]
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]  # Truncate long messages
            if role in ["user", "assistant"] and content:
                prefix = "Patient" if role == "user" else "Monica"
                summary_parts.append(f"{prefix}: {content}")

        return "\n".join(summary_parts)

    async def process_message(self, user_message: str) -> str:
        """Process an incoming SMS and generate a response."""
        # Add user message to history
        self.messages.append({"role": "user", "content": user_message})

        # Build messages for LLM
        llm_messages = [
            {"role": "system", "content": self._get_system_prompt()},
            *self.messages,
        ]

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",  # Fast and cheap for text
                messages=llm_messages,
                max_tokens=200,  # Keep responses short
                temperature=0.7,
            )

            assistant_message = response.choices[0].message.content.strip()

            # Add to history
            self.messages.append({"role": "assistant", "content": assistant_message})

            logger.info(f"Text conversation - Patient: {user_message[:50]}... → Response: {assistant_message[:50]}...")

            return assistant_message

        except Exception as e:
            logger.error(f"Error processing text message: {e}")
            return "Sorry, I'm having trouble right now. Please call our office or try again in a moment."

    def get_handoff_message(self) -> str:
        """Generate the initial SMS sent when transitioning from voice."""
        state = self.state
        first_name = state.get("first_name", "")
        appointment_date = state.get("appointment_date", "")
        appointment_time = state.get("appointment_time", "")

        if appointment_date and appointment_time:
            return f"""Hi{' ' + first_name if first_name else ''}! This is Monica from {self.organization_name}.

Your appointment: {appointment_date} at {appointment_time}

Reply anytime with questions, or text:
• RESCHEDULE to change
• CANCEL to cancel
• STAFF for a callback"""
        else:
            return f"""Hi{' ' + first_name if first_name else ''}! This is Monica from {self.organization_name}.

I'm here if you have any questions. Just reply to this message anytime!

Text STAFF if you'd like someone to call you back."""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize conversation state for storage."""
        return {
            "patient_id": self.patient_id,
            "organization_id": self.organization_id,
            "organization_name": self.organization_name,
            "state": self.state,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TextConversation":
        """Restore conversation from stored state."""
        conv = cls(
            patient_id=data["patient_id"],
            organization_id=data["organization_id"],
            organization_name=data.get("organization_name", "Demo Clinic Alpha"),
            initial_context=data.get("state", {}),
        )
        conv.messages = data.get("messages", [])
        return conv
