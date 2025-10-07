from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import Frame, TranscriptionFrame, InterimTranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
import time
import logging
import re

logger = logging.getLogger(__name__)

class HoldDetector(FrameProcessor):
    
    HOLD_PHRASES = [
        r'\bhold\b',
        r'\bplease hold\b',
        r'\bone moment\b',
        r'\bjust a moment\b',
        r'\bgive me a moment\b',
        r'\bgive me a second\b',
        r'\blet me check\b',
        r'\blet me pull that up\b',
        r'\blet me look\b',
        r'\bbear with me\b',
        r'\bstay on the line\b',
        r'\bhold on\b',
    ]
    
    def __init__(self, silence_threshold=15.0, tentative_timeout=5.0, **kwargs):
        super().__init__(**kwargs)
        self.flow_manager = None
        self.silence_threshold = silence_threshold
        self.tentative_timeout = tentative_timeout
        self.last_speech_time = time.time()
        self._last_logged_status = None
        self.hold_pattern = re.compile('|'.join(self.HOLD_PHRASES), re.IGNORECASE)
        logger.info(f"HoldDetector initialized (silence={silence_threshold}s, tentative={tentative_timeout}s)")
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        # Monitor for assistant acknowledgment before entering hold
        if self.flow_manager:
            hold_pending = self.flow_manager.state.get("hold_pending", False)
            
            # If assistant responded while hold is pending, enter hold state
            if hold_pending and hasattr(frame, 'text') and isinstance(frame, TranscriptionFrame):
                # Check if this is an assistant message (you might need to check frame role/direction)
                # For now, assume any text after hold_pending means assistant responded
                logger.info("[HoldDetector] Assistant acknowledged, entering hold state")
                await self._transition_to_hold()
        
        # Check if frame has text attribute
        if hasattr(frame, 'text') and frame.text and self.flow_manager:
            if isinstance(frame, TranscriptionFrame):
                logger.info(f"[HoldDetector] Processing: '{frame.text}'")
                await self._handle_transcription(frame)
                
                hold_status = self.flow_manager.state.get("hold_status", "active")
                if hold_status == "on_hold":
                    logger.debug("[HoldDetector] Blocking frame - on hold")
                    return
        
        await self.push_frame(frame, direction)
    
    async def _handle_transcription(self, frame):
        """Analyze transcription and update hold state"""
        text = frame.text.strip()
        current_status = self.flow_manager.state.get("hold_status", "active")
        current_time = time.time()
        
        if text:
            self.last_speech_time = current_time
            
            # Check for hold keywords
            if current_status == "active" and self.hold_pattern.search(text):
                logger.warning(f"🔴 Hold phrase detected: '{text}'")
                # Set pending flag - will enter hold after agent acknowledges
                self.flow_manager.state["hold_pending"] = True
                # DON'T block this frame - let LLM respond
                return
            
            if current_status == "on_hold":
                await self._transition_to_tentative_return()
            
            elif current_status == "tentative_return":
                await self._transition_to_active()
        
        else:
            silence_duration = current_time - self.last_speech_time
            
            if current_status == "active" and silence_duration > self.silence_threshold:
                await self._transition_to_hold()
            
            elif current_status == "tentative_return" and silence_duration > self.tentative_timeout:
                await self._transition_to_hold()
    
    async def _transition_to_hold(self):
        """Enter hold state"""
        current_node = self.flow_manager._current_node_name if hasattr(self.flow_manager, '_current_node_name') else None
        self.flow_manager.state["previous_node"] = current_node
        self.flow_manager.state["hold_status"] = "on_hold"
        self.flow_manager.state["hold_pending"] = False  # Clear pending flag
        
        if self._last_logged_status != "on_hold":
            logger.warning(f"🔴 ENTERED HOLD STATE - Now blocking LLM/TTS (was in: {current_node})")
            self._last_logged_status = "on_hold"
    
    async def _transition_to_tentative_return(self):
        """Enter tentative return state"""
        self.flow_manager.state["hold_status"] = "tentative_return"
        
        if self._last_logged_status != "tentative_return":
            logger.warning(f"🟡 POSSIBLE RETURN - Saying 'Yes, I'm here'")
            self._last_logged_status = "tentative_return"
        
        from flow_nodes import create_hold_return_node
        patient_data = self.flow_manager.state.get("patient_data", {})
        return_node = create_hold_return_node(patient_data)
        
        try:
            await self.flow_manager.set_node(return_node)
        except Exception as e:
            logger.error(f"Failed to transition to hold_return node: {e}")
    
    async def _transition_to_active(self):
        """Resume normal conversation"""
        previous_node = self.flow_manager.state.get("previous_node")
        self.flow_manager.state["hold_status"] = "active"
        self.flow_manager.state["returning_from_hold"] = True
        
        if self._last_logged_status != "active":
            logger.warning(f"🟢 RESUMED CONVERSATION - Returning to: {previous_node}")
            self._last_logged_status = "active"
        
        if previous_node:
            from flow_nodes import (
                create_patient_verification_node,
                create_authorization_check_node,
                create_greeting_node
            )
            
            patient_data = self.flow_manager.state.get("patient_data", {})
            
            if previous_node == "patient_verification":
                resume_node = create_patient_verification_node(patient_data, returning_from_hold=True)
            elif previous_node == "authorization_check":
                resume_node = create_authorization_check_node(patient_data, returning_from_hold=True)
            elif previous_node == "greeting":
                resume_node = create_greeting_node(patient_data)
            else:
                return
            
            try:
                await self.flow_manager.set_node(resume_node)
                self.flow_manager.state["returning_from_hold"] = False
            except Exception as e:
                logger.error(f"Failed to resume previous node: {e}")