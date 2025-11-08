from typing import Dict, Any, Union
from loguru import logger
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
from backend.functions import PATIENT_TOOLS, update_prior_auth_status_handler


class ServiceFactory:
    @staticmethod
    def create_transport(
        config: Dict[str, Any],
        room_url: str,
        room_token: str,
        room_name: str
    ) -> DailyTransport:
        logger.info("📞 Creating Daily transport service")
        transport = DailyTransport(
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
        logger.info("✅ Daily transport service created")
        return transport
    
    @staticmethod
    def create_stt(config: Dict[str, Any]) -> DeepgramFluxSTTService:
        logger.info("🎤 Creating Deepgram Flux STT service")
        params = DeepgramFluxSTTService.InputParams(
            eager_eot_threshold=config.get('eager_eot_threshold'),
            eot_threshold=config.get('eot_threshold'),
            eot_timeout_ms=config.get('eot_timeout_ms'),
            keyterm=config.get('keyterm', []),
            mip_opt_out=config.get('mip_opt_out'),
            tag=config.get('tag', [])
        )

        service = DeepgramFluxSTTService(
            api_key=config['api_key'],
            model=config.get('model', 'flux-general-en'),
            params=params
        )

        logger.info("✅ Deepgram Flux STT service created")
        return service
    
    @staticmethod
    def create_llm(config: Dict[str, Any]):
        provider = config.get('provider', 'openai')
        logger.info(f"🤖 Creating {provider.upper()} main LLM service")

        if provider == 'groq':
            llm = GroqLLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=config.get('temperature', 0.4)
            )
        elif provider == 'anthropic':
            llm = AnthropicLLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=config.get('temperature', 0.4)
            )
        elif provider == 'openai':
            llm = OpenAILLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=config.get('temperature', 0.4)
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}. Supported providers: openai, groq, anthropic")

        llm.register_function("update_prior_auth_status", update_prior_auth_status_handler)

        logger.info(f"✅ {provider.upper()} main LLM service created with update_prior_auth_status handler registered")
        return llm

    @staticmethod
    def create_classifier_llm(config: Dict[str, Any]):
        provider = config.get('provider', 'openai')
        logger.info(f"⚡ Creating {provider.upper()} classifier LLM service")

        if provider == 'groq':
            llm = GroqLLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=0,
                max_tokens=10
            )
        elif provider == 'anthropic':
            llm = AnthropicLLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=0,
                max_tokens=10
            )
        elif provider == 'openai':
            llm = OpenAILLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=0,
                max_tokens=10
            )
        else:
            raise ValueError(f"Unsupported classifier LLM provider: {provider}. Supported providers: openai, groq, anthropic")

        logger.info(f"✅ {provider.upper()} classifier LLM service created")
        return llm

    @staticmethod
    def create_tts(config: Dict[str, Any]) -> ElevenLabsTTSService:
        logger.info("🗣️ Creating ElevenLabs TTS service")
        params = ElevenLabsTTSService.InputParams(
            stability=config.get('stability'),
            similarity_boost=config.get('similarity_boost'),
            style=config.get('style', 0.0),
            enable_ssml_parsing=True
        )

        service = ElevenLabsTTSService(
            api_key=config['api_key'],
            voice_id=config['voice_id'],
            model=config['model'],
            params=params
        )
        logger.info("✅ ElevenLabs TTS service created")
        return service

    @staticmethod
    def create_llm_switcher(config: Dict[str, Any]) -> tuple:
        logger.info("🔀 Creating LLM switcher")
        classifier_llm = ServiceFactory.create_classifier_llm(config['classifier_llm'])
        main_llm = ServiceFactory.create_llm(config['llm'])

        llm_switcher = LLMSwitcher(
            llms=[classifier_llm, main_llm],
            strategy_type=ServiceSwitcherStrategyManual
        )

        logger.info("✅ LLM switcher created")
        return llm_switcher, classifier_llm, main_llm