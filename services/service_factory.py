from typing import Any, Dict

from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.daily.transport import DailyDialinSettings, DailyParams, DailyTransport

from utils.function_call_text_filter import FunctionCallTextFilter
from utils.spelling_text_filter import SpellingTextFilter


class ServiceFactory:
    @staticmethod
    def create_transport(
        config: Dict[str, Any],
        room_url: str,
        room_token: str,
        room_name: str,
        dialin_settings: Dict[str, str] = None
    ) -> DailyTransport:
        """Create Daily transport for telephony calls."""
        params_dict = {
            'api_key': config['api_key'],
            'audio_in_enabled': True,
            'audio_out_enabled': True,
            'transcription_enabled': False
        }

        if dialin_settings:
            params_dict['dialin_settings'] = DailyDialinSettings(
                call_id=dialin_settings['call_id'],
                call_domain=dialin_settings['call_domain']
            )
        else:
            params_dict['phone_number_id'] = config['phone_number_id']

        return DailyTransport(
            room_url,
            room_token,
            room_name,
            params=DailyParams(**params_dict)
        )

    @staticmethod
    def create_stt(config: Dict[str, Any]) -> DeepgramFluxSTTService:
        """Create Deepgram Flux STT service (has built-in turn detection)."""
        optional_params = ['eager_eot_threshold', 'eot_threshold', 'eot_timeout_ms',
                          'keyterm', 'mip_opt_out', 'tag']
        params_dict = {k: config[k] for k in optional_params if config.get(k) is not None}

        return DeepgramFluxSTTService(
            api_key=config['api_key'],
            model=config.get('model', 'flux-general-en'),
            params=DeepgramFluxSTTService.InputParams(**params_dict)
        )

    @staticmethod
    def create_llm(config: Dict[str, Any], is_classifier: bool = False):
        """Create LLM service with retry on timeout enabled."""
        provider = config.get('provider', 'openai')

        kwargs = {
            'api_key': config['api_key'],
            'model': config['model'],
            'temperature': 0 if is_classifier else config.get('temperature', 0.4),
        }

        max_tokens = 10 if is_classifier else config.get('max_tokens')
        if max_tokens:
            kwargs['max_tokens'] = max_tokens

        # Provider-specific params with retry on timeout
        if provider == 'openai':
            params_kwargs = {'retry_on_timeout': True}
            if not is_classifier and config.get('service_tier'):
                params_kwargs['service_tier'] = config['service_tier']
            kwargs['params'] = OpenAILLMService.InputParams(**params_kwargs)
        elif provider == 'groq':
            kwargs['params'] = GroqLLMService.InputParams(retry_on_timeout=True)
        elif provider == 'anthropic':
            kwargs['params'] = AnthropicLLMService.InputParams(retry_on_timeout=True)

        providers = {
            'groq': GroqLLMService,
            'anthropic': AnthropicLLMService,
            'openai': OpenAILLMService,
        }

        if provider not in providers:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        return providers[provider](**kwargs)

    @staticmethod
    def create_tts(config: Dict[str, Any]) -> CartesiaTTSService:
        """Create Cartesia TTS service."""
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

        return CartesiaTTSService(
            api_key=config['api_key'],
            voice_id=config['voice_id'],
            model=config['model'],
            params=params,
            aggregate_sentences=config.get('aggregate_sentences', True),
            text_filters=[FunctionCallTextFilter(), SpellingTextFilter()]
        )
