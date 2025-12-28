from loguru import logger
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.llm_service import LLMService

from pipeline.safety_processors import PassThroughProcessor, SafetyClassifier
from pipeline.safety_prompts import SAFETY_CLASSIFICATION_PROMPT


class SafetyMonitor(ParallelPipeline):
    def __init__(self, *, safety_llm: LLMService):
        self._context = LLMContext(
            messages=[{"role": "system", "content": SAFETY_CLASSIFICATION_PROMPT}]
        )
        self._context_aggregator = LLMContextAggregatorPair(self._context)
        self._safety_classifier = SafetyClassifier()

        super().__init__(
            [PassThroughProcessor()],
            [
                self._context_aggregator.user(),
                safety_llm,
                self._safety_classifier,
                self._context_aggregator.assistant(),
            ],
        )

        self._register_event_handler("on_emergency_detected")
        self._register_event_handler("on_staff_requested")

        logger.info("SafetyMonitor initialized")

    def add_event_handler(self, event_name: str, handler):
        if event_name in ("on_emergency_detected", "on_staff_requested"):
            self._safety_classifier.add_event_handler(event_name, handler)
        else:
            super().add_event_handler(event_name, handler)
