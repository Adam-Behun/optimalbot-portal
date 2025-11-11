import logging
import os
import yaml
from pathlib import Path
from typing import Dict, Any
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat_flows import FlowManager

from services.service_factory import ServiceFactory
from pipeline.fixed_ivr_navigator import FixedIVRNavigator
from core.flow_loader import FlowLoader

logger = logging.getLogger(__name__)


class PipelineFactory:

    @staticmethod
    def build(
        client_name: str,
        session_data: Dict[str, Any],
        room_config: Dict[str, str]
    ) -> tuple:
        services_config = PipelineFactory._load_services_config(client_name)

        main_llm = ServiceFactory.create_llm(
            services_config['services']['llm']
        )
        classifier_llm = ServiceFactory.create_classifier_llm(
            services_config['services']['classifier_llm']
        )

        llm_switcher = LLMSwitcher(
            llms=[main_llm, classifier_llm],
            strategy_type=ServiceSwitcherStrategyManual
        )

        services = {
            'stt': ServiceFactory.create_stt(services_config['services']['stt']),
            'tts': ServiceFactory.create_tts(services_config['services']['tts']),
            'transport': ServiceFactory.create_transport(
                services_config['services']['transport'],
                room_config['room_url'],
                room_config['room_token'],
                room_config['room_name']
            ),
            'main_llm': main_llm,
            'classifier_llm': classifier_llm,
            'llm_switcher': llm_switcher
        }

        components = PipelineFactory._create_conversation_components(
            client_name,
            session_data,
            services
        )

        pipeline, params = PipelineFactory._assemble_pipeline(services, components)

        return pipeline, params, services['transport'], components

    @staticmethod
    def _load_services_config(client_name: str) -> Dict[str, Any]:
        """Load and parse services.yaml for a client."""
        client_path = Path(f"clients/{client_name}")
        services_path = client_path / "services.yaml"

        with open(services_path, 'r') as f:
            config = yaml.safe_load(f)

        return PipelineFactory._substitute_env_vars(config)

    @staticmethod
    def _substitute_env_vars(config: Dict[str, Any]) -> Dict[str, Any]:
        """Substitute ${ENV_VAR} placeholders with environment variables."""
        for key, value in config.items():
            if isinstance(value, dict):
                config[key] = PipelineFactory._substitute_env_vars(value)
            elif isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                env_var_name = value[2:-1]
                env_value = os.getenv(env_var_name)
                if env_value is None:
                    raise ValueError(f"Required environment variable '{env_var_name}' is not set")
                config[key] = env_value
        return config

    @staticmethod
    def _create_conversation_components(
        client_name: str,
        session_data: Dict[str, Any],
        services: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create FlowManager and conversation components."""

        context = LLMContext()
        context_aggregator = LLMContextAggregatorPair(context)

        transcript_processor = TranscriptProcessor()

        flow_loader = FlowLoader(client_name)
        FlowClass = flow_loader.load_flow_class()

        # IVRNavigator starts with classifier_llm for fast IVR vs conversation detection
        # Will switch to main_llm only when IVR system is actually detected
        ivr_navigator = FixedIVRNavigator(
            llm=services['classifier_llm'],
            ivr_prompt="Navigate to provider services for prior authorization verification",
            ivr_vad_params=VADParams(stop_secs=2.0)
        )

        flow = FlowClass(
            patient_data=session_data['patient_data'],
            flow_manager=None,
            main_llm=services['main_llm'],
            classifier_llm=services['classifier_llm'],
            context_aggregator=context_aggregator
        )

        return {
            'context_aggregator': context_aggregator,
            'transcript_processor': transcript_processor,
            'ivr_navigator': ivr_navigator,
            'flow': flow,
            'main_llm': services['main_llm'],
            'classifier_llm': services['classifier_llm'],
            'llm_switcher': services['llm_switcher']
        }

    @staticmethod
    def _assemble_pipeline(
        services: Dict[str, Any],
        components: Dict[str, Any]
    ) -> tuple[Pipeline, PipelineParams]:
        # IVRNavigator replaces the LLM in the pipeline (it contains the LLM internally)
        # See: https://docs.pipecat.ai/guides/fundamentals/ivr-navigator
        pipeline = Pipeline([
            services['transport'].input(),
            services['stt'],
            components['transcript_processor'].user(),
            components['context_aggregator'].user(),
            components['ivr_navigator'],  # Contains llm_switcher internally, replaces LLM
            services['tts'],
            components['transcript_processor'].assistant(),
            components['context_aggregator'].assistant(),
            services['transport'].output()
        ])

        # Configure pipeline-wide audio sample rates
        # Deepgram Flux STT expects 16kHz input (telephony standard)
        # ElevenLabs TTS outputs 24kHz (high quality)
        params = PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True
        )

        return pipeline, params
