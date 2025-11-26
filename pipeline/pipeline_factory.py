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
from pipecat.processors.filters.stt_mute_filter import STTMuteConfig, STTMuteFilter, STTMuteStrategy
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
        room_config: Dict[str, str],
        dialin_settings: Dict[str, str] = None
    ) -> tuple:
        organization_slug = session_data.get('organization_slug')
        services_config = PipelineFactory._load_services_config(organization_slug, client_name)

        # Extract call_type from services config (required field)
        call_type = services_config.get('call_type')
        if not call_type:
            raise ValueError(f"Missing required 'call_type' in services.yaml for {organization_slug}/{client_name}")

        main_llm = ServiceFactory.create_llm(
            services_config['services']['llm']
        )

        # classifier_llm and LLM switching are optional
        # If classifier_llm is not configured, use main_llm directly without switching
        classifier_llm_config = services_config['services'].get('classifier_llm')
        if classifier_llm_config:
            classifier_llm = ServiceFactory.create_classifier_llm(classifier_llm_config)
            # LLMSwitcher starts with classifier_llm as default (first in list)
            # Flow pre_actions will switch to main_llm when function calling is needed
            llm_switcher = LLMSwitcher(
                llms=[classifier_llm, main_llm],
                strategy_type=ServiceSwitcherStrategyManual
            )
            active_llm = llm_switcher
        else:
            # Single LLM mode - no switching needed
            classifier_llm = None
            llm_switcher = None
            active_llm = main_llm
            logger.info("Single LLM mode: classifier_llm not configured, using main_llm only")

        services = {
            'stt': ServiceFactory.create_stt(services_config['services']['stt']),
            'tts': ServiceFactory.create_tts(services_config['services']['tts']),
            'transport': ServiceFactory.create_transport(
                services_config['services']['transport'],
                room_config['room_url'],
                room_config['room_token'],
                room_config['room_name'],
                dialin_settings
            ),
            'main_llm': main_llm,
            'classifier_llm': classifier_llm,
            'llm_switcher': llm_switcher,
            'active_llm': active_llm  # The LLM to use in pipeline (switcher or main_llm)
        }

        components = PipelineFactory._create_conversation_components(
            client_name,
            session_data,
            services,
            call_type
        )

        pipeline, params = PipelineFactory._assemble_pipeline(services, components)

        return pipeline, params, services['transport'], components

    @staticmethod
    def _load_services_config(organization_slug: str, client_name: str) -> Dict[str, Any]:
        """Load and parse services.yaml for a client."""
        client_path = Path(f"clients/{organization_slug}/{client_name}")
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
        services: Dict[str, Any],
        call_type: str
    ) -> Dict[str, Any]:
        """Create FlowManager and conversation components."""

        context = LLMContext()
        context_aggregator = LLMContextAggregatorPair(context)

        transcript_processor = TranscriptProcessor()

        organization_slug = session_data.get('organization_slug')
        organization_id = session_data.get('organization_id')
        flow_loader = FlowLoader(organization_slug, client_name)
        FlowClass = flow_loader.load_flow_class()

        # IVRNavigator uses active_llm (either llm_switcher or main_llm directly)
        # When llm_switcher is available: starts with classifier_llm for fast detection,
        # switches to main_llm when IVR is detected (handlers/ivr.py manages switches)
        # When single LLM mode: uses main_llm directly without switching
        ivr_navigator = FixedIVRNavigator(
            llm=services['active_llm'],
            ivr_prompt="Navigate to provider services for prior authorization verification",
            ivr_vad_params=VADParams(stop_secs=2.0)
        )

        flow = FlowClass(
            patient_data=session_data['patient_data'],
            flow_manager=None,
            main_llm=services['main_llm'],
            classifier_llm=services['classifier_llm'],
            context_aggregator=context_aggregator,
            organization_id=organization_id
        )

        return {
            'context_aggregator': context_aggregator,
            'transcript_processor': transcript_processor,
            'ivr_navigator': ivr_navigator,
            'flow': flow,
            'main_llm': services['main_llm'],
            'classifier_llm': services['classifier_llm'],
            'llm_switcher': services['llm_switcher'],
            'active_llm': services['active_llm'],
            'call_type': call_type
        }

    @staticmethod
    def _assemble_pipeline(
        services: Dict[str, Any],
        components: Dict[str, Any]
    ) -> tuple[Pipeline, PipelineParams]:
        # Create STTMuteFilter to prevent interruptions during bot's first speech (greeting)
        # Uses FIRST_SPEECH strategy: mutes user input during the initial greeting utterance,
        # then automatically unmutes after greeting completes
        stt_mute_processor = STTMuteFilter(
            config=STTMuteConfig(
                strategies={STTMuteStrategy.FIRST_SPEECH}
            )
        )

        # IVRNavigator replaces the LLM in the pipeline (it contains the LLM internally)
        # See: https://docs.pipecat.ai/guides/fundamentals/ivr-navigator
        pipeline = Pipeline([
            services['transport'].input(),
            services['stt'],
            stt_mute_processor,  # Mute user input during first speech (greeting)
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
