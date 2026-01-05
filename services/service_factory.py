from typing import Any, Dict, Optional

from loguru import logger
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.daily.transport import DailyDialinSettings, DailyParams, DailyTransport

from utils.function_call_text_filter import FunctionCallTextFilter


class LLMFallbackError(Exception):
    """Raised when all LLM providers have failed."""
    pass


class FallbackLLMWrapper:
    """
    Wrapper that holds primary and fallback LLM services for automatic failover.

    Triggers fallback on:
    - Rate limit errors (HTTP 429)
    - Server errors (5xx)
    - Timeout errors
    - Circuit breaker open

    Usage:
        wrapper = FallbackLLMWrapper(primary_llm, fallback_llm)
        # Access active LLM for pipeline
        pipeline_llm = wrapper.active

        # On error, switch to fallback
        if should_fallback(error):
            wrapper.switch_to_fallback()
    """

    def __init__(
        self,
        primary: Any,
        fallback: Any,
        primary_name: str = "openai",
        fallback_name: str = "anthropic",
    ):
        self.primary = primary
        self.fallback = fallback
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self._active = primary
        self._active_name = primary_name
        self._using_fallback = False

    @property
    def active(self) -> Any:
        """Get the currently active LLM service."""
        return self._active

    @property
    def active_name(self) -> str:
        """Get the name of the currently active LLM."""
        return self._active_name

    @property
    def using_fallback(self) -> bool:
        """Check if currently using fallback LLM."""
        return self._using_fallback

    def should_fallback(self, error: Exception) -> bool:
        """Determine if an error should trigger fallback to secondary LLM."""
        error_str = str(error).lower()

        # Rate limit errors
        if "429" in error_str or "rate limit" in error_str or "rate_limit" in error_str:
            return True

        # Server errors
        if any(code in error_str for code in ["500", "502", "503", "504"]):
            return True

        # Timeout errors
        if "timeout" in error_str or "timed out" in error_str:
            return True

        # Circuit breaker
        if "circuit" in error_str:
            return True

        # Anthropic overloaded
        if "overloaded" in error_str:
            return True

        return False

    def switch_to_fallback(self) -> bool:
        """
        Switch to fallback LLM.

        Returns:
            True if switched, False if already on fallback
        """
        if self._active == self.primary and self.fallback is not None:
            self._active = self.fallback
            self._active_name = self.fallback_name
            self._using_fallback = True
            logger.warning(f"LLM switched from {self.primary_name} to {self.fallback_name}")
            return True
        return False

    def switch_to_primary(self) -> bool:
        """
        Switch back to primary LLM.

        Returns:
            True if switched, False if already on primary
        """
        if self._active == self.fallback:
            self._active = self.primary
            self._active_name = self.primary_name
            self._using_fallback = False
            logger.info(f"LLM switched back to {self.primary_name}")
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Get current fallback wrapper status."""
        return {
            "primary": self.primary_name,
            "fallback": self.fallback_name,
            "active": self._active_name,
            "using_fallback": self._using_fallback,
        }


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
        provider = config.get('provider', 'openai')

        kwargs = {
            'api_key': config['api_key'],
            'model': config['model'],
            'temperature': 0 if is_classifier else config.get('temperature', 0.4),
        }

        max_tokens = 10 if is_classifier else config.get('max_tokens')
        if max_tokens:
            kwargs['max_tokens'] = max_tokens

        # OpenAI-specific: service_tier
        if provider == 'openai' and not is_classifier and config.get('service_tier'):
            kwargs['params'] = OpenAILLMService.InputParams(service_tier=config['service_tier'])

        providers = {
            'groq': GroqLLMService,
            'anthropic': AnthropicLLMService,
            'openai': OpenAILLMService,
        }

        if provider not in providers:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        return providers[provider](**kwargs)

    @staticmethod
    def create_llm_with_fallback(
        primary_config: Dict[str, Any],
        fallback_config: Optional[Dict[str, Any]] = None,
        is_classifier: bool = False,
    ) -> FallbackLLMWrapper:
        """
        Create LLM service with optional fallback for automatic failover.

        Args:
            primary_config: Config for primary LLM (usually OpenAI)
            fallback_config: Config for fallback LLM (usually Anthropic). If None, no fallback.
            is_classifier: If True, uses classifier settings (low temp, low tokens)

        Returns:
            FallbackLLMWrapper containing primary and optional fallback LLM

        Example config:
            primary_config = {
                'provider': 'openai',
                'api_key': '...',
                'model': 'gpt-4o',
                'temperature': 0.4
            }
            fallback_config = {
                'provider': 'anthropic',
                'api_key': '...',
                'model': 'claude-sonnet-4-20250514',
                'temperature': 0.4
            }
        """
        primary = ServiceFactory.create_llm(primary_config, is_classifier)
        primary_name = primary_config.get('provider', 'openai')

        fallback = None
        fallback_name = None
        if fallback_config:
            fallback = ServiceFactory.create_llm(fallback_config, is_classifier)
            fallback_name = fallback_config.get('provider', 'anthropic')
            logger.info(f"LLM fallback configured: {primary_name} -> {fallback_name}")

        return FallbackLLMWrapper(
            primary=primary,
            fallback=fallback,
            primary_name=primary_name,
            fallback_name=fallback_name or "none",
        )

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
            text_filters=[FunctionCallTextFilter()]
        )
