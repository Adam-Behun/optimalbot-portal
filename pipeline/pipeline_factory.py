import os
from loguru import logger
import yaml
from pathlib import Path
from typing import Dict, Any
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.processors.filters.stt_mute_filter import STTMuteConfig, STTMuteFilter, STTMuteStrategy
from pipecat_flows import FlowManager

from services.service_factory import ServiceFactory
from pipeline.triage_detector import TriageDetector
from pipeline.ivr_navigation_processor import IVRNavigationProcessor
from core.flow_loader import FlowLoader


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

        # classifier_llm is used only in TriageDetector parallel pipeline for classification
        # The main pipeline always uses main_llm - no switching needed
        classifier_llm_config = services_config['services'].get('classifier_llm')
        if classifier_llm_config:
            classifier_llm = ServiceFactory.create_classifier_llm(classifier_llm_config)
        else:
            classifier_llm = None
            logger.info("classifier_llm not configured - triage detection disabled")

        # Main pipeline always uses main_llm (no LLM switching)
        active_llm = main_llm

        # Extract turn_detection config if present (for Smart Turn + VAD)
        turn_detection_config = services_config.get('turn_detection')

        services = {
            'stt': ServiceFactory.create_stt(services_config['services']['stt']),
            'tts': ServiceFactory.create_tts(services_config['services']['tts']),
            'transport': ServiceFactory.create_transport(
                services_config['services']['transport'],
                room_config['room_url'],
                room_config['room_token'],
                room_config['room_name'],
                dialin_settings,
                turn_detection_config
            ),
            'main_llm': main_llm,
            'classifier_llm': classifier_llm,
            'active_llm': active_llm,
            'cold_transfer': services_config.get('cold_transfer')
        }

        components = PipelineFactory._create_conversation_components(
            client_name,
            session_data,
            services,
            call_type,
            services_config
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
        call_type: str,
        services_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create FlowManager and conversation components."""

        context = LLMContext()
        context_aggregator = LLMContextAggregatorPair(context)

        # Store context separately for direct access in handlers
        # (LLMContextAggregatorPair doesn't expose _context in all pipecat versions)

        transcript_processor = TranscriptProcessor()

        organization_slug = session_data.get('organization_slug')
        organization_id = session_data.get('organization_id')
        flow_loader = FlowLoader(organization_slug, client_name)
        FlowClass = flow_loader.load_flow_class()

        flow_kwargs = {
            'patient_data': session_data['patient_data'],
            'flow_manager': None,
            'main_llm': services['main_llm'],
            'context_aggregator': context_aggregator,
            'organization_id': organization_id
        }
        if services['classifier_llm']:
            flow_kwargs['classifier_llm'] = services['classifier_llm']
        if services.get('cold_transfer'):
            flow_kwargs['cold_transfer_config'] = services['cold_transfer']

        flow = FlowClass(**flow_kwargs)

        triage_detector = None
        ivr_processor = None

        if call_type == "dial-out":
            triage_config = services_config.get('triage', {})

            if triage_config.get('enabled', True) and services['classifier_llm']:
                flow_triage_config = flow.get_triage_config()

                triage_detector = TriageDetector(
                    classifier_llm=services['classifier_llm'],
                    classifier_prompt=flow_triage_config['classifier_prompt'],
                    voicemail_response_delay=triage_config.get('voicemail_response_delay', 2.0),
                )

                # 2.0s longer pause for IVR menus which have longer prompts
                ivr_processor = IVRNavigationProcessor(
                    ivr_vad_params=VADParams(stop_secs=2.0)
                )

        return {
            'context': context,
            'context_aggregator': context_aggregator,
            'transcript_processor': transcript_processor,
            'triage_detector': triage_detector,
            'ivr_processor': ivr_processor,
            'flow': flow,
            'main_llm': services['main_llm'],
            'classifier_llm': services['classifier_llm'],
            'active_llm': services['active_llm'],
            'call_type': call_type
        }

    @staticmethod
    def _assemble_pipeline(
        services: Dict[str, Any],
        components: Dict[str, Any]
    ) -> tuple[Pipeline, PipelineParams]:

        stt_mute_processor = STTMuteFilter(
            config=STTMuteConfig(strategies={STTMuteStrategy.FIRST_SPEECH})
        )

        if components.get('triage_detector'):
            pipeline = Pipeline([
                services['transport'].input(),
                services['stt'],
                components['triage_detector'].detector(),
                components['ivr_processor'],
                stt_mute_processor,
                components['transcript_processor'].user(),
                components['context_aggregator'].user(),
                components['active_llm'],
                services['tts'],
                components['triage_detector'].gate(),
                components['transcript_processor'].assistant(),
                components['context_aggregator'].assistant(),
                services['transport'].output()
            ])
        else:
            pipeline = Pipeline([
                services['transport'].input(),
                services['stt'],
                stt_mute_processor,
                components['transcript_processor'].user(),
                components['context_aggregator'].user(),
                components['active_llm'],
                services['tts'],
                components['transcript_processor'].assistant(),
                components['context_aggregator'].assistant(),
                services['transport'].output()
            ])

        params = PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True
        )

        return pipeline, params
