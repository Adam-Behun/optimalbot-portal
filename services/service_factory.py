"""Service Factory - Creates service instances from configuration"""

from typing import Dict, Any
from deepgram import LiveOptions
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from backend.functions import PATIENT_TOOLS, update_prior_auth_status_handler


class ServiceFactory:
    """Creates Pipecat service instances from parsed YAML configuration"""
    
    @staticmethod
    def create_vad_analyzer() -> SileroVADAnalyzer:
        """Create VAD analyzer with default params"""
        return SileroVADAnalyzer(params=VADParams(
            confidence=0.7, 
            start_secs=0.2, 
            stop_secs=0.8, 
            min_volume=0.5
        ))
    
    @staticmethod
    def create_transport(
        config: Dict[str, Any],
        room_url: str,
        room_token: str,
        room_name: str,
        vad_analyzer: SileroVADAnalyzer
    ) -> DailyTransport:
        """Create Daily transport with telephony support"""
        return DailyTransport(
            room_url,
            room_token,
            room_name,
            params=DailyParams(
                audio_in_sample_rate=16000,
                audio_in_channels=1,
                audio_out_enabled=True,
                audio_out_sample_rate=24000,
                audio_out_channels=1,
                transcription_enabled=False,
                vad_analyzer=vad_analyzer,
                vad_enabled=True,
                vad_audio_passthrough=True,
                api_key=config['api_key'],
                phone_number_id=config['phone_number_id']
            )
        )
    
    @staticmethod
    def create_stt(config: Dict[str, Any]):
        """Create Deepgram STT service from YAML configuration

        Supports both traditional Deepgram STT and Deepgram Flux STT.
        Set use_flux: true in config to enable Flux model.
        """
        use_flux = config.get('use_flux', False)

        if use_flux:
            logger.info(f"🎤 Creating Deepgram Flux STT service")

            # Basic pattern (matches Pipecat example): stt = DeepgramFluxSTTService(api_key=...)
            service = DeepgramFluxSTTService(api_key=config['api_key'])

            logger.info(f"✅ Deepgram Flux STT service created successfully")
            return service
        else:
            # Traditional Deepgram STT
            logger.info(f"🎤 Creating Deepgram STT service with model: {config.get('model', 'NOT SET')}")
            logger.info(f"   Endpointing: {config.get('endpointing')}ms")
            logger.info(f"   Interim results: {config.get('interim_results')}")
            logger.info(f"   VAD events: {config.get('vad_events')}")

            # Build LiveOptions with ALL configuration from services.yaml
            live_options = LiveOptions(
                model=config.get('model', 'nova-2-general'),
                endpointing=config.get('endpointing', 400),
                language=config.get('language', 'en-US'),
                interim_results=config.get('interim_results', False),
                smart_format=config.get('smart_format', True),
                punctuate=config.get('punctuate', True),
                vad_events=config.get('vad_events', False),
            )

            # Create service
            service = DeepgramSTTService(
                api_key=config['api_key'],
                live_options=live_options
            )

            logger.info(f"✅ Deepgram STT service created successfully")
            return service
    
    @staticmethod
    def create_llm(config: Dict[str, Any]) -> OpenAILLMService:
        """Create main LLM with function registration"""
        llm = OpenAILLMService(
            api_key=config['api_key'],
            model=config['model'],
            temperature=config['temperature']
        )
        llm.register_function("update_prior_auth_status", update_prior_auth_status_handler)
        return llm

    @staticmethod
    def create_classifier_llm(config: Dict[str, Any]) -> OpenAILLMService:
        """Create fast classifier LLM without tools for IVR detection"""
        llm = OpenAILLMService(
            api_key=config['api_key'],
            model=config['model'],
            temperature=0,  # Deterministic classification
            max_tokens=10   # Only need "<mode>conversation</mode>"
        )
        # Register function handler (needed when tools are enabled mid-conversation)
        llm.register_function("update_prior_auth_status", update_prior_auth_status_handler)
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