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

        params_dict = {}
        if 'eager_eot_threshold' in config and config['eager_eot_threshold'] is not None:
            params_dict['eager_eot_threshold'] = config['eager_eot_threshold']
        if 'eot_threshold' in config and config['eot_threshold'] is not None:
            params_dict['eot_threshold'] = config['eot_threshold']
        if 'eot_timeout_ms' in config and config['eot_timeout_ms'] is not None:
            params_dict['eot_timeout_ms'] = config['eot_timeout_ms']
        if 'keyterm' in config and config['keyterm']:
            params_dict['keyterm'] = config['keyterm']
        if 'mip_opt_out' in config and config['mip_opt_out'] is not None:
            params_dict['mip_opt_out'] = config['mip_opt_out']
        if 'tag' in config and config['tag']:
            params_dict['tag'] = config['tag']

        params = DeepgramFluxSTTService.InputParams(**params_dict)

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

        # Build pronunciation dictionary locators if provided
        pronunciation_dict_locators = None
        if config.get('pronunciation_dictionary_locators'):
            from pipecat.services.elevenlabs.tts import PronunciationDictionaryLocator
            pronunciation_dict_locators = [
                PronunciationDictionaryLocator(
                    pronunciation_dictionary_id=locator['pronunciation_dictionary_id'],
                    version_id=locator['version_id']
                )
                for locator in config['pronunciation_dictionary_locators']
            ]

        # Build input params with all available settings
        params = ElevenLabsTTSService.InputParams(
            language=config.get('language'),  # Language enum if specified
            stability=config.get('stability'),
            similarity_boost=config.get('similarity_boost'),
            style=config.get('style'),
            use_speaker_boost=config.get('use_speaker_boost'),
            speed=config.get('speed'),
            auto_mode=config.get('auto_mode', True),  # Default to True for optimal performance
            enable_ssml_parsing=config.get('enable_ssml_parsing', True),  # Default to True for SSML support
            enable_logging=config.get('enable_logging'),
            apply_text_normalization=config.get('apply_text_normalization', 'auto'),  # 'auto', 'on', or 'off'
            pronunciation_dictionary_locators=pronunciation_dict_locators
        )

        service = ElevenLabsTTSService(
            api_key=config['api_key'],
            voice_id=config['voice_id'],
            model=config['model'],
            params=params,
            aggregate_sentences=config.get('aggregate_sentences', True)
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