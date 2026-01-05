import os
from pathlib import Path
from typing import Any, Dict

import yaml
from loguru import logger
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.filters.stt_mute_filter import STTMuteConfig, STTMuteFilter, STTMuteStrategy
from pipecat.processors.transcript_processor import TranscriptProcessor

from core.flow_loader import FlowLoader
from pipeline.ivr_navigation_processor import IVRNavigationProcessor
from pipeline.safety_processors import OutputValidator, SafetyMonitor
from pipeline.triage_detector import TriageDetector
from pipeline.types import ConversationComponents
from services.service_factory import FallbackLLMWrapper, ServiceFactory


class PipelineFactory:

    @staticmethod
    def build(
        client_name: str,
        session_data: Dict[str, Any],
        room_config: Dict[str, str],
        dialin_settings: Dict[str, str] = None
    ) -> tuple:
        organization_slug = session_data.get('organization_slug')
        services_config = PipelineFactory.load_services_config(organization_slug, client_name)

        call_type = services_config.get('call_type')
        if not call_type:
            raise ValueError(f"Missing 'call_type' in services.yaml for {client_name}")

        # Create services directly
        transport = ServiceFactory.create_transport(
            services_config['services']['transport'],
            room_config['room_url'],
            room_config['room_token'],
            room_config['room_name'],
            dialin_settings
        )
        stt = ServiceFactory.create_stt(services_config['services']['stt'])
        tts = ServiceFactory.create_tts(services_config['services']['tts'])

        # Create main LLM with optional fallback
        llm_config = services_config['services']['llm']
        fallback_llm_config = services_config['services'].get('fallback_llm')

        if fallback_llm_config:
            llm_wrapper = ServiceFactory.create_llm_with_fallback(llm_config, fallback_llm_config)
            main_llm = llm_wrapper.active  # Use active LLM for pipeline
        else:
            llm_wrapper = None
            main_llm = ServiceFactory.create_llm(llm_config)

        classifier_llm_config = services_config['services'].get('classifier_llm')
        if classifier_llm_config:
            classifier_llm = ServiceFactory.create_llm(classifier_llm_config, is_classifier=True)
        else:
            classifier_llm = None
            logger.info("classifier_llm not configured - triage detection disabled")

        components = PipelineFactory._create_conversation_components(
            client_name=client_name,
            session_data=session_data,
            transport=transport,
            stt=stt,
            tts=tts,
            main_llm=main_llm,
            classifier_llm=classifier_llm,
            call_type=call_type,
            services_config=services_config,
            llm_wrapper=llm_wrapper,
        )

        pipeline, params = PipelineFactory._assemble_pipeline(components)

        return pipeline, params, components

    @staticmethod
    def load_services_config(organization_slug: str, client_name: str) -> Dict[str, Any]:
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
        transport: Any,
        stt: Any,
        tts: Any,
        main_llm: Any,
        classifier_llm: Any,
        call_type: str,
        services_config: Dict[str, Any],
        llm_wrapper: FallbackLLMWrapper = None,
    ) -> ConversationComponents:
        """Create flow and conversation components."""
        context = LLMContext()
        context_aggregator = LLMContextAggregatorPair(context)
        transcript_processor = TranscriptProcessor()

        organization_slug = session_data.get('organization_slug')
        organization_id = session_data.get('organization_id')
        flow_loader = FlowLoader(organization_slug, client_name)
        FlowClass = flow_loader.load_flow_class()

        flow_kwargs = {
            'call_data': session_data['call_data'],
            'session_id': session_data['session_id'],
            'flow_manager': None,
            'main_llm': main_llm,
            'context_aggregator': context_aggregator,
            'organization_id': organization_id
        }
        if classifier_llm:
            flow_kwargs['classifier_llm'] = classifier_llm
        cold_transfer_config = services_config.get('cold_transfer')
        if cold_transfer_config:
            flow_kwargs['cold_transfer_config'] = cold_transfer_config

        flow = FlowClass(**flow_kwargs)

        triage_detector = None
        ivr_processor = None

        if call_type == "dial-out":
            triage_config = services_config.get('triage', {})

            if triage_config.get('enabled', True) and classifier_llm:
                flow_triage_config = flow.get_triage_config()

                triage_detector = TriageDetector(
                    classifier_llm=classifier_llm,
                    classifier_prompt=flow_triage_config['classifier_prompt'],
                    voicemail_response_delay=triage_config.get('voicemail_response_delay', 2.0),
                )

                ivr_processor = IVRNavigationProcessor(
                    ivr_vad_params=VADParams(stop_secs=2.0)
                )

        safety_config = services_config.get('safety_monitors', {})
        safety_llm_config = safety_config.get('safety_llm')

        # Create safety monitor with graceful degradation
        safety_monitor = None
        if safety_config.get('enabled') and safety_llm_config:
            try:
                safety_monitor = SafetyMonitor(
                    api_key=safety_llm_config['api_key'],
                    model=safety_llm_config.get('model', 'meta-llama/llama-guard-4-12b')
                )
            except Exception as e:
                logger.warning(f"SafetyMonitor init failed, continuing without: {e}")
                safety_monitor = None

        # Create output validator with graceful degradation
        output_validator = None
        if safety_config.get('output_validator', {}).get('enabled') and safety_llm_config:
            try:
                output_validator = OutputValidator(
                    api_key=safety_llm_config['api_key'],
                    model=safety_llm_config.get('model', 'meta-llama/llama-guard-4-12b')
                )
            except Exception as e:
                logger.warning(f"OutputValidator init failed, continuing without: {e}")
                output_validator = None

        return ConversationComponents(
            transport=transport,
            stt=stt,
            tts=tts,
            main_llm=main_llm,
            active_llm=main_llm,
            context=context,
            context_aggregator=context_aggregator,
            transcript_processor=transcript_processor,
            flow=flow,
            call_type=call_type,
            classifier_llm=classifier_llm,
            triage_detector=triage_detector,
            ivr_processor=ivr_processor,
            safety_monitor=safety_monitor,
            output_validator=output_validator,
            safety_config=safety_config,
            llm_wrapper=llm_wrapper,
        )

    @staticmethod
    def _assemble_pipeline(components: ConversationComponents) -> tuple[Pipeline, PipelineParams]:
        stt_mute_processor = STTMuteFilter(
            config=STTMuteConfig(strategies={STTMuteStrategy.FIRST_SPEECH})
        )

        processors = [components.transport.input(), components.stt]

        if components.safety_monitor:
            processors.append(components.safety_monitor)

        if components.triage_detector:
            processors.extend([
                components.triage_detector.detector(),
                components.ivr_processor,
            ])

        processors.extend([
            stt_mute_processor,
            components.transcript_processor.user(),
            components.context_aggregator.user(),
            components.active_llm,
        ])

        if components.output_validator:
            processors.append(components.output_validator)

        processors.append(components.tts)

        if components.triage_detector:
            processors.append(components.triage_detector.gate())

        processors.extend([
            components.transcript_processor.assistant(),
            components.context_aggregator.assistant(),
            components.transport.output()
        ])

        pipeline = Pipeline(processors)

        params = PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True
        )

        return pipeline, params
