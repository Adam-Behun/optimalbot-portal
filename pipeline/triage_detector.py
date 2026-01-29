"""Triage Detector - 3-way call classification using parallel pipeline."""

import asyncio

from loguru import logger
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.llm_service import LLMService
from pipecat.utils.sync.event_notifier import EventNotifier

from pipeline.triage_processors import (
    ClassifierGate,
    ClassifierUpstreamGate,
    MainBranchGate,
    TriageProcessor,
    TTSGate,
)


class TriageDetector(ParallelPipeline):
    """Parallel pipeline for 3-way call classification.

    Classifies incoming audio as CONVERSATION, IVR, or VOICEMAIL.

    Events:
        on_conversation_detected(conversation_history): Human answered
        on_ivr_detected(conversation_history): IVR menu detected
        on_voicemail_detected(): Voicemail system detected
    """

    def __init__(
        self,
        *,
        classifier_llm: LLMService,
        classifier_prompt: str,
        # 2.0s delay allows voicemail beep to complete before speaking
        voicemail_response_delay: float = 2.0,
    ):
        """Initialize the triage detector.

        Args:
            classifier_llm: Fast LLM for classification (e.g., Groq)
            classifier_prompt: System prompt for 3-way classification
            voicemail_response_delay: Seconds to wait after VM detected before speaking
        """
        self._classifier_llm = classifier_llm
        self._classifier_prompt = classifier_prompt
        self._voicemail_response_delay = voicemail_response_delay

        messages = [{"role": "system", "content": classifier_prompt}]
        self._context = LLMContext(messages)
        self._context_aggregator = LLMContextAggregatorPair(self._context)

        self._gate_notifier = EventNotifier()
        self._conversation_notifier = EventNotifier()
        self._ivr_notifier = EventNotifier()
        self._voicemail_notifier = EventNotifier()
        self._ivr_completed_notifier = EventNotifier()

        self._main_branch_gate = MainBranchGate(
            conversation_notifier=self._conversation_notifier,
            ivr_notifier=self._ivr_notifier,
            ivr_completed_notifier=self._ivr_completed_notifier,
        )

        self._classifier_gate = ClassifierGate(
            gate_notifier=self._gate_notifier,
            conversation_notifier=self._conversation_notifier,
        )

        self._triage_processor = TriageProcessor(
            gate_notifier=self._gate_notifier,
            conversation_notifier=self._conversation_notifier,
            ivr_notifier=self._ivr_notifier,
            voicemail_notifier=self._voicemail_notifier,
            voicemail_response_delay=voicemail_response_delay,
            context=self._context,
        )

        self._tts_gate = TTSGate(
            conversation_notifier=self._conversation_notifier,
            ivr_notifier=self._ivr_notifier,
            voicemail_notifier=self._voicemail_notifier,
        )

        self._classifier_upstream_gate = ClassifierUpstreamGate(
            gate_notifier=self._gate_notifier,
        )

        super().__init__(
            [self._main_branch_gate],
            [
                self._classifier_gate,
                self._context_aggregator.user(),
                self._classifier_llm,
                self._triage_processor,
                self._context_aggregator.assistant(),
                self._classifier_upstream_gate,  # blocks upstream frames after decision
            ],
        )

        self._register_event_handler("on_conversation_detected")
        self._register_event_handler("on_ivr_detected")
        self._register_event_handler("on_voicemail_detected")

        logger.info("TriageDetector initialized")

    def detector(self) -> "TriageDetector":
        """Returns self for pipeline placement after STT."""
        return self

    def gate(self) -> TTSGate:
        """Returns TTSGate for pipeline placement after TTS."""
        return self._tts_gate

    def notify_ivr_completed(self):
        """Signal that IVR navigation completed - opens main gate."""
        asyncio.create_task(self._ivr_completed_notifier.notify())

    def add_event_handler(self, event_name: str, handler):
        """Route event handlers to triage processor."""
        if event_name in ("on_conversation_detected", "on_ivr_detected", "on_voicemail_detected"):
            self._triage_processor.add_event_handler(event_name, handler)
        else:
            super().add_event_handler(event_name, handler)
