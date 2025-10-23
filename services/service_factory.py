"""Service Factory - Creates service instances from configuration"""

from typing import Dict, Any
from deepgram import LiveOptions
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.services.daily import DailyParams, DailyTransport
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
    def create_stt(config: Dict[str, Any]) -> DeepgramSTTService:
        """Create Deepgram STT service"""
        return DeepgramSTTService(
            api_key=config['api_key'],
            model=config['model'],
            options=LiveOptions(endpointing=config['endpointing'])
        )
    
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
    def create_tts(config: Dict[str, Any]) -> ElevenLabsTTSService:
        """Create ElevenLabs TTS service"""
        return ElevenLabsTTSService(
            api_key=config['api_key'],
            voice_id=config['voice_id'],
            model=config['model'],
            stability=config['stability']
        )