"""
Event models for monitoring system.
Uses Pydantic for validation and serialization.
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Literal
from datetime import datetime
from uuid import uuid4


class MonitoringEvent(BaseModel):
    """Base event model - all events inherit from this."""
    
    id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    category: Literal["CALL", "INTENT", "TRANSITION", "PROMPT", "LLM", "TRANSCRIPT", "LATENCY", "ERROR", "DETECTION"]
    event: str
    severity: Literal["debug", "info", "warning", "error"] = "info"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: Optional[float] = None
    tags: List[str] = Field(default_factory=list)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class CallEvent(MonitoringEvent):
    """Call lifecycle events."""
    category: Literal["CALL"] = "CALL"
    
    class Metadata(BaseModel):
        patient_id: Optional[str] = None
        patient_name: Optional[str] = None
        phone_number: Optional[str] = None
        insurance_company: Optional[str] = None


class IntentEvent(MonitoringEvent):
    """Intent classification events."""
    category: Literal["INTENT"] = "INTENT"
    
    class Metadata(BaseModel):
        intent: str
        message: str
        method: Literal["llm", "keyword", "unknown"]
        confidence: Optional[float] = None


class TransitionEvent(MonitoringEvent):
    """State transition events."""
    category: Literal["TRANSITION"] = "TRANSITION"
    
    class Metadata(BaseModel):
        from_state: str
        to_state: str
        trigger: str
        duration_in_state_ms: Optional[float] = None


class PromptEvent(MonitoringEvent):
    """Prompt rendering events."""
    category: Literal["PROMPT"] = "PROMPT"
    
    class Metadata(BaseModel):
        state: str
        prompt_length: int
        template: Optional[str] = None
        render_time_ms: Optional[float] = None


class LLMEvent(MonitoringEvent):
    """LLM interaction events."""
    category: Literal["LLM"] = "LLM"
    
    class Metadata(BaseModel):
        model: str
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        ttft_ms: Optional[float] = None  # Time to first token
        total_latency_ms: Optional[float] = None


class TranscriptEvent(MonitoringEvent):
    """Conversation transcript events."""
    category: Literal["TRANSCRIPT"] = "TRANSCRIPT"
    
    class Metadata(BaseModel):
        role: Literal["user", "assistant"]
        content: str
        word_count: Optional[int] = None
        duration_ms: Optional[float] = None


class LatencyEvent(MonitoringEvent):
    """End-to-end latency tracking."""
    category: Literal["LATENCY"] = "LATENCY"
    
    class Metadata(BaseModel):
        stage: str
        cumulative_ms: Optional[float] = None  # Cumulative from user stop speaking


class ErrorEvent(MonitoringEvent):
    """Error tracking events."""
    category: Literal["ERROR"] = "ERROR"
    severity: Literal["error"] = "error"
    
    class Metadata(BaseModel):
        error_type: str
        error_message: str
        stack_trace: Optional[str] = None
        recoverable: bool = False


class CallMetrics(BaseModel):
    """Aggregated metrics for a call."""
    session_id: str
    total_duration_seconds: float
    state_transitions: int
    intents_detected: int
    avg_e2e_latency_ms: Optional[float] = None
    llm_calls: int
    transcript_messages: int
    errors: int
    current_state: Optional[str] = None


class LatencyMetrics(BaseModel):
    """Latency breakdown for a call."""
    session_id: str
    avg_stt_ms: Optional[float] = None
    avg_intent_ms: Optional[float] = None
    avg_llm_ttft_ms: Optional[float] = None
    avg_llm_total_ms: Optional[float] = None
    avg_tts_ms: Optional[float] = None
    avg_e2e_ms: Optional[float] = None
    
    # Percentiles
    p50_e2e_ms: Optional[float] = None
    p95_e2e_ms: Optional[float] = None
    p99_e2e_ms: Optional[float] = None
    
    samples: int = 0


class IntentMetrics(BaseModel):
    """Intent classification statistics."""
    session_id: str
    intent_breakdown: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    total_classifications: int = 0
    llm_usage_percent: float = 0.0
    avg_latency_ms: float = 0.0