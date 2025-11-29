from typing import Dict, Any
from loguru import logger
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.transports.daily.transport import DailyDialinSettings, DailyParams, DailyTransport
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual


class ServiceFactory:
    @staticmethod
    def create_transport(
        config: Dict[str, Any],
        room_url: str,
        room_token: str,
        room_name: str,
        dialin_settings: Dict[str, str] = None
    ) -> DailyTransport:
        # Build DailyParams based on call type
        params_dict = {
            'api_key': config['api_key'],
            'audio_in_enabled': True,
            'audio_out_enabled': True,
            'transcription_enabled': False
        }

        if dialin_settings:
            # Dial-in: use DailyDialinSettings
            daily_dialin_settings = DailyDialinSettings(
                call_id=dialin_settings['call_id'],
                call_domain=dialin_settings['call_domain']
            )
            params_dict['dialin_settings'] = daily_dialin_settings
        else:
            # Dial-out: use phone_number_id
            params_dict['phone_number_id'] = config['phone_number_id']

        transport = DailyTransport(
            room_url,
            room_token,
            room_name,
            params=DailyParams(**params_dict)
        )
        return transport

    @staticmethod
    def create_stt(config: Dict[str, Any]) -> DeepgramFluxSTTService:
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

        return service

    @staticmethod
    def create_llm(config: Dict[str, Any]):
        provider = config.get('provider', 'openai')

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
            params = None
            if config.get('service_tier'):
                params = OpenAILLMService.InputParams(
                    service_tier=config['service_tier']
                )
            llm = OpenAILLMService(
                api_key=config['api_key'],
                model=config['model'],
                temperature=config.get('temperature', 0.4),
                params=params
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}. Supported providers: openai, groq, anthropic")

        return llm

    @staticmethod
    def create_classifier_llm(config: Dict[str, Any]):
        provider = config.get('provider', 'openai')

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

        return llm

    @staticmethod
    def create_tts(config: Dict[str, Any]):
        """Create TTS service based on provider configuration."""
        provider = config.get('provider', 'elevenlabs')  # Default to elevenlabs for backwards compatibility

        if provider == 'cartesia':
            return ServiceFactory._create_cartesia_tts(config)
        elif provider == 'elevenlabs':
            return ServiceFactory._create_elevenlabs_tts(config)
        else:
            raise ValueError(f"Unsupported TTS provider: {provider}. Supported providers: elevenlabs, cartesia")

    @staticmethod
    def _create_cartesia_tts(config: Dict[str, Any]) -> CartesiaTTSService:
        """Create Cartesia TTS service instance."""
        # Build generation config if provided
        generation_config = None
        if config.get('generation_config'):
            gc = config['generation_config']
            generation_config = GenerationConfig(
                speed=gc.get('speed'),
                volume=gc.get('volume'),
                emotion=gc.get('emotion')
            )

        params = CartesiaTTSService.InputParams(
            generation_config=generation_config
        )

        service = CartesiaTTSService(
            api_key=config['api_key'],
            voice_id=config['voice_id'],
            model=config['model'],
            params=params,
            aggregate_sentences=config.get('aggregate_sentences', True)
        )
        return service

    @staticmethod
    def _create_elevenlabs_tts(config: Dict[str, Any]) -> ElevenLabsTTSService:
        """Create ElevenLabs TTS service instance."""
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
        return service

    @staticmethod
    def create_llm_switcher(config: Dict[str, Any]) -> tuple:
        classifier_llm = ServiceFactory.create_classifier_llm(config['classifier_llm'])
        main_llm = ServiceFactory.create_llm(config['llm'])

        llm_switcher = LLMSwitcher(
            llms=[classifier_llm, main_llm],
            strategy_type=ServiceSwitcherStrategyManual
        )

        return llm_switcher, classifier_llm, main_llm