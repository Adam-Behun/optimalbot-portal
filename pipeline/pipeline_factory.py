import logging
from typing import Dict, Any
from openai._types import NOT_GIVEN
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.extensions.ivr.ivr_navigator import IVRNavigator
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.transcript_processor import TranscriptProcessor

from services.service_factory import ServiceFactory
from pipeline.audio_processors import AudioResampler, DropEmptyAudio, StateTagStripper, CodeFormatter, LLMTransitionMonitor
from core.context import ConversationContext
from core.state_manager import StateManager
from backend.functions import PATIENT_TOOLS
from handlers.ivr import IVRTranscriptProcessor

logger = logging.getLogger(__name__)


class PipelineFactory:
    
    @staticmethod
    def build(
        client_config: Any,
        session_data: Dict[str, Any],
        room_config: Dict[str, str]
    ) -> tuple:
        logger.info("Building pipeline")
        services_config = client_config.services_config

        llm_switcher, classifier_llm, main_llm = ServiceFactory.create_llm_switcher(services_config['services'])

        services = {
            'stt': ServiceFactory.create_stt(services_config['services']['stt']),
            'tts': ServiceFactory.create_tts(services_config['services']['tts']),
            'llm_switcher': llm_switcher,
            'classifier_llm': classifier_llm,
            'main_llm': main_llm,
            'transport': ServiceFactory.create_transport(
                services_config['services']['transport'],
                room_config['room_url'],
                room_config['room_token'],
                room_config['room_name']
            )
        }

        logger.info("Creating conversation components")
        components = PipelineFactory._create_conversation_components(
            client_config,
            session_data,
            services
        )

        logger.info("Assembling pipeline")
        pipeline = PipelineFactory._assemble_pipeline(services, components)

        logger.info("Pipeline build complete")
        return pipeline, services['transport'], components
    
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
            patient_id=session_data['patient_id'],
            main_llm=services['main_llm'],
            classifier_llm=services['classifier_llm']
        )

        logger.debug("Creating transcript processor")
        transcript_processor = TranscriptProcessor()

        logger.debug("Setting up LLM context")
        initial_prompt = context.render_prompt()
        llm_context = OpenAILLMContext(
            messages=[{"role": "system", "content": initial_prompt}],
            tools=NOT_GIVEN
        )
        context_aggregators = services['main_llm'].create_context_aggregator(llm_context)

        state_manager.set_context_aggregators(context_aggregators)

        formatted_data = client_config.data_formatter.format_patient_data(
            session_data['patient_data']
        )

        logger.debug("Configuring IVR navigator")
        call_classifier_prompt = client_config.prompt_renderer.render_prompt(
            "call_classifier", "system", {}
        )

        ivr_goal = client_config.prompt_renderer.render_prompt(
            "ivr_navigation", "task", {}
        ) or "Navigate to provider services for eligibility verification"

        ivr_navigator = IVRNavigator(
            llm=services['llm_switcher'],
            ivr_prompt=ivr_goal,
            ivr_vad_params=VADParams(stop_secs=2.0)
        )

        if call_classifier_prompt:
            ivr_navigator._classifier_prompt = call_classifier_prompt
            ivr_navigator._ivr_processor._classifier_prompt = call_classifier_prompt

        logger.debug("Creating IVR transcript processor")
        ivr_transcript_processor = IVRTranscriptProcessor(session_data['transcripts'])

        logger.debug("Creating LLM transition monitor")
        monitor = LLMTransitionMonitor(state_manager=state_manager)
        state_manager.monitor = monitor

        logger.debug(f"Components created - Initial state: {context.current_state}")
        return {
            'context': context,
            'state_manager': state_manager,
            'transcript_processor': transcript_processor,
            'context_aggregators': context_aggregators,
            'ivr_navigator': ivr_navigator,
            'ivr_transcript_processor': ivr_transcript_processor,
            'monitor': monitor,
            'llm_switcher': services['llm_switcher'],
            'main_llm': services['main_llm'],
            'classifier_llm': services['classifier_llm']
        }
    
    @staticmethod
    def _assemble_pipeline(
        services: Dict[str, Any],
        components: Dict[str, Any]
    ) -> Pipeline:
        return Pipeline([
            services['transport'].input(),
            AudioResampler(target_sample_rate=16000),
            DropEmptyAudio(),
            services['stt'],
            components['transcript_processor'].user(),
            components['context_aggregators'].user(),
            components['ivr_navigator'],
            components['ivr_transcript_processor'],
            components['monitor'],
            StateTagStripper(state_manager=components['state_manager']),
            CodeFormatter(),
            services['tts'],
            components['transcript_processor'].assistant(),
            components['context_aggregators'].assistant(),
            services['transport'].output()
        ])