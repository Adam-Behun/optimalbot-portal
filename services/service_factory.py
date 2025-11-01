"""Service Factory - Creates service instances from configuration"""

from typing import Dict, Any, Union
from loguru import logger
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
from backend.functions import PATIENT_TOOLS, update_prior_auth_status_handler


class ServiceFactory:
    """Creates Pipecat service instances from parsed YAML configuration"""

    @staticmethod
    def create_transport(
        config: Dict[str, Any],
        room_url: str,
        room_token: str,
        room_name: str
    ) -> DailyTransport:
        """Create Daily transport with telephony support"""
        return DailyTransport(
            room_url,
            room_token,
            room_name,
            params=DailyParams(
                audio_in_enabled=True,
                audio_in_sample_rate=16000,
                audio_in_channels=1,
                audio_out_enabled=True,
                audio_out_sample_rate=24000,
                audio_out_channels=1,
                transcription_enabled=False,
                api_key=config['api_key'],
                phone_number_id=config['phone_number_id']
            )
        )
    
    @staticmethod
    def create_stt(config: Dict[str, Any]) -> DeepgramFluxSTTService:
        """Create Deepgram Flux STT service from YAML configuration

        Flux provides built-in turn detection via EagerEndOfTurn and EndOfTurn events.
        """
        logger.info("🎤 Creating Deepgram Flux STT service")

        # Build InputParams from config
        params = DeepgramFluxSTTService.InputParams(
            eager_eot_threshold=config.get('eager_eot_threshold'),
            eot_threshold=config.get('eot_threshold'),
            eot_timeout_ms=config.get('eot_timeout_ms'),
            keyterm=config.get('keyterm', []),
            mip_opt_out=config.get('mip_opt_out'),
            tag=config.get('tag', [])
        )

        # Create service
        service = DeepgramFluxSTTService(
            api_key=config['api_key'],
            model=config.get('model', 'flux-general-en'),
            params=params
        )

        logger.info("✅ Deepgram Flux STT service created")
        return service
    
    @staticmethod
    def create_llm(config: Dict[str, Any]):
        """Create main LLM with function registration"""
        provider = config.get('provider', 'openai')

        if provider == 'groq':
            llm = GroqLLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=config.get('temperature', 0.4)
            )
        else:  # default to openai
            llm = OpenAILLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=config['temperature']
            )

        llm.register_function("update_prior_auth_status", update_prior_auth_status_handler)
        return llm

    @staticmethod
    def create_classifier_llm(config: Dict[str, Any]):
        """Create fast classifier LLM without tools for IVR detection"""
        provider = config.get('provider', 'openai')

        if provider == 'groq':
            llm = GroqLLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=0,  # Deterministic classification
                max_tokens=10   # Only need "<mode>conversation</mode>"
            )
        else:  # default to openai
            llm = OpenAILLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=0,  # Deterministic classification
                max_tokens=10   # Only need "<mode>conversation</mode>"
            )

        # No function registration for classifier - it only does IVR vs Human detection
        return llm

    @staticmethod
    def create_tts(config: Dict[str, Any]) -> ElevenLabsTTSService:
        """Create ElevenLabs TTS service with SSML support"""
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService as ElevenLabsService

        params = ElevenLabsService.InputParams(
            stability=config.get('stability'),
            similarity_boost=config.get('similarity_boost'),
            style=config.get('style', 0.0),
            enable_ssml_parsing=True  # Enable SSML for code pronunciation control
        )

        return ElevenLabsTTSService(
            api_key=config['api_key'],
            voice_id=config['voice_id'],
            model=config['model'],
            params=params
        )

    @staticmethod
    def create_llm_switcher(config: Dict[str, Any]) -> tuple:
        """Create LLM switcher managing classifier and main LLM

        The switcher starts with classifier_llm active (for initial IVR/CONVERSATION detection).
        To switch to main_llm, push ManuallySwitchServiceFrame(service=main_llm) to pipeline.

        Returns:
            tuple: (llm_switcher, classifier_llm, main_llm)
        """
        # Create both LLMs separately
        classifier_llm = ServiceFactory.create_classifier_llm(config['classifier_llm'])
        main_llm = ServiceFactory.create_llm(config['llm'])

        # Create switcher with both LLMs
        # Manual strategy defaults to first LLM (classifier_llm) as active
        llm_switcher = LLMSwitcher(
            llms=[classifier_llm, main_llm],
            strategy_type=ServiceSwitcherStrategyManual
        )

        return llm_switcher, classifier_llm, main_llm