import os
from pathlib import Path
from typing import Any, Dict

import yaml
from loguru import logger
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSTextFrame
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.consumer_processor import ConsumerProcessor
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.producer_processor import ProducerProcessor
from pipecat.turns.mute import FirstSpeechUserMuteStrategy
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

from core.flow_loader import FlowLoader
from pipeline.ivr_human_detector import IVRHumanDetector
from pipeline.ivr_navigation_processor import IVRNavigationProcessor
from pipeline.observer import ObserverContextManager, create_observer_branch
from pipeline.safety_processors import OutputValidator, SafetyMonitor
from pipeline.transcript_logger import TranscriptLogger
from pipeline.triage_detector import TriageDetector
from pipeline.types import ConversationComponents
from services.service_factory import ServiceFactory


async def _is_tts_text_frame(frame):
    """Filter for ProducerProcessor: matches TTSTextFrame."""
    return isinstance(frame, TTSTextFrame)


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

        # Create main LLM
        llm_config = services_config['services']['llm']
        main_llm = ServiceFactory.create_llm(llm_config)

        classifier_llm_config = services_config['services'].get('classifier_llm')
        if classifier_llm_config:
            classifier_llm = ServiceFactory.create_llm(classifier_llm_config, is_classifier=True)
        else:
            classifier_llm = None
            logger.info("classifier_llm not configured - triage detection disabled")

        # Create observer LLM (optional)
        observer_llm_config = services_config['services'].get('observer_llm')
        observer_llm = None
        if observer_llm_config:
            observer_llm = ServiceFactory.create_llm(observer_llm_config)
            logger.info("Observer LLM created for silent data extraction")

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
            observer_llm=observer_llm,
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
        observer_llm: Any = None,
    ) -> ConversationComponents:
        """Create flow and conversation components."""
        context = LLMContext()
        # ExternalUserTurnStrategies is designed for STT services like Deepgram Flux
        # that handle turn detection and interruptions themselves via
        # UserStartedSpeakingFrame / UserStoppedSpeakingFrame
        context_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=ExternalUserTurnStrategies(),
                user_mute_strategies=[FirstSpeechUserMuteStrategy()],
            )
        )

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
        ivr_human_detector = None

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

                # IVR human detection uses direct Groq API calls
                classifier_config = services_config['services'].get('classifier_llm', {})
                if classifier_config.get('provider') == 'groq':
                    ivr_human_detector = IVRHumanDetector(
                        api_key=classifier_config['api_key'],
                        model=classifier_config.get('model', 'llama-3.3-70b-versatile')
                    )
                else:
                    logger.info("IVR human detection disabled (requires Groq classifier)")

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

        # Create observer components if observer LLM is configured and flow supports it
        observer_context_manager = None
        bot_speech_producer = None
        if observer_llm and hasattr(flow, 'get_observer_system_prompt'):
            observer_prompt = flow.get_observer_system_prompt()
            observer_tools = flow.get_observer_tools()
            if observer_prompt and observer_tools:
                # Get extraction field names from flow if available
                extraction_fields = None
                if hasattr(flow, 'get_extraction_fields'):
                    extraction_fields = flow.get_extraction_fields()

                observer_context_manager = ObserverContextManager(
                    system_prompt=observer_prompt,
                    tools=observer_tools,
                    tool_choice="required",
                    flow_ref=flow,
                    extraction_fields=extraction_fields,
                )

                bot_speech_producer = ProducerProcessor(
                    filter=_is_tts_text_frame,
                    passthrough=True,
                )
                logger.info("Observer pipeline branch configured")

        return ConversationComponents(
            transport=transport,
            stt=stt,
            tts=tts,
            main_llm=main_llm,
            active_llm=main_llm,
            context=context,
            context_aggregator=context_aggregator,
            flow=flow,
            call_type=call_type,
            classifier_llm=classifier_llm,
            triage_detector=triage_detector,
            ivr_processor=ivr_processor,
            ivr_human_detector=ivr_human_detector,
            safety_monitor=safety_monitor,
            output_validator=output_validator,
            safety_config=safety_config,
            observer_llm=observer_llm,
            observer_context_manager=observer_context_manager,
            bot_speech_producer=bot_speech_producer,
        )

    @staticmethod
    def _assemble_pipeline(components: ConversationComponents) -> tuple[Pipeline, PipelineParams]:
        # Pre-processors: transport input -> STT -> transcript logger -> safety -> triage -> IVR
        pre_processors = [components.transport.input(), components.stt, TranscriptLogger()]

        if components.safety_monitor:
            pre_processors.append(components.safety_monitor)

        if components.triage_detector:
            pre_processors.append(components.triage_detector.detector())

        if components.ivr_human_detector:
            pre_processors.append(components.ivr_human_detector)

        # Build conversational processors (shared between observer and flat pipeline)
        conv_processors = [
            components.context_aggregator.user(),
            components.active_llm,
        ]
        if components.ivr_processor:
            conv_processors.append(components.ivr_processor)
        if components.output_validator:
            conv_processors.append(components.output_validator)
        conv_processors.append(components.tts)
        if components.triage_detector:
            conv_processors.append(components.triage_detector.gate())
        conv_processors.append(components.context_aggregator.assistant())

        # Check if we have an observer branch
        has_observer = (
            components.observer_llm
            and components.observer_context_manager
            and components.bot_speech_producer
        )

        if has_observer:
            # Conv branch ends with bot_speech_producer to share TTSTextFrame
            conv_branch = conv_processors + [components.bot_speech_producer]

            # Observer branch: receives bot speech via consumer, builds context, runs observer LLM
            bot_speech_consumer = ConsumerProcessor(
                producer=components.bot_speech_producer,
                direction=FrameDirection.DOWNSTREAM,
            )
            observer_branch = create_observer_branch(
                components.observer_context_manager,
                components.observer_llm,
                bot_speech_consumer,
            )

            parallel = ParallelPipeline(conv_branch, observer_branch)
            processors = pre_processors + [parallel, components.transport.output()]
            logger.info("Pipeline assembled with ParallelPipeline (conv + observer)")
        else:
            # Flat pipeline (backward compatible)
            processors = pre_processors + conv_processors + [components.transport.output()]

        pipeline = Pipeline(processors)

        params = PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
            enable_usage_metrics=True
        )

        return pipeline, params
