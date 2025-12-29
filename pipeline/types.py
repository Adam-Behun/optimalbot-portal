from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConversationComponents:
    """All services and processors for the conversation pipeline."""
    # Required fields (no defaults) - must come first
    transport: Any
    stt: Any
    tts: Any
    main_llm: Any
    active_llm: Any
    context: Any
    context_aggregator: Any
    transcript_processor: Any
    flow: Any
    call_type: str

    # Optional fields (with defaults) - must come last
    classifier_llm: Optional[Any] = None
    triage_detector: Optional[Any] = None
    ivr_processor: Optional[Any] = None
    safety_monitor: Optional[Any] = None
    output_validator: Optional[Any] = None
    safety_config: dict = field(default_factory=dict)
