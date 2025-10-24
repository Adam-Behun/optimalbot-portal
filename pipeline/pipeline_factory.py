"""
Pipeline factory for assembling Pipecat pipelines.
Creates services, handlers, and wires them into a complete pipeline.
"""

from typing import Dict, Any
from openai._types import NOT_GIVEN
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.extensions.ivr.ivr_navigator import IVRNavigator
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.transcript_processor import TranscriptProcessor

from services.service_factory import ServiceFactory
from pipeline.audio_processors import AudioResampler, DropEmptyAudio, StateTagStripper, SSMLCodeFormatter
from core.context import ConversationContext
from core.state_manager import StateManager
from backend.functions import PATIENT_TOOLS


class PipelineFactory:
    """Builds Pipecat pipeline instances for voice conversations"""
    
    @staticmethod
    def build(
        client_config: Any,
        session_data: Dict[str, Any],
        room_config: Dict[str, str]
    ) -> tuple:
        """
        Build complete pipeline with all services and handlers.
        
        Args:
            client_config: ClientConfig with schema, prompts, services
            session_data: session_id, patient_id, patient_data, phone_number
            room_config: room_url, room_token, room_name
            
        Returns:
            tuple: (pipeline, transport, components)
        """
        services_config = client_config.services_config
        
        # Create VAD analyzer
        vad_analyzer = ServiceFactory.create_vad_analyzer()
        
        # Create services
        services = {
            'stt': ServiceFactory.create_stt(services_config['services']['stt']),
            'tts': ServiceFactory.create_tts(services_config['services']['tts']),
            'llm': ServiceFactory.create_llm(services_config['services']['llm']),
            'classifier_llm': ServiceFactory.create_classifier_llm(services_config['services']['classifier_llm']),
            'transport': ServiceFactory.create_transport(
                services_config['services']['transport'],
                room_config['room_url'],
                room_config['room_token'],
                room_config['room_name'],
                vad_analyzer
            )
        }
        
        # Create conversation components
        components = PipelineFactory._create_conversation_components(
            client_config,
            session_data,
            services
        )
        
        # Assemble pipeline
        pipeline = PipelineFactory._assemble_pipeline(services, components)
        
        return pipeline, services['transport'], components
    
    @staticmethod
    def _create_conversation_components(
        client_config,
        session_data: Dict[str, Any],
        services: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create conversation context, state manager, and handlers"""
        # Create conversation context
        context = ConversationContext(
            schema=client_config.schema,
            patient_data=session_data['patient_data'],
            session_id=session_data['session_id'],
            prompt_renderer=client_config.prompt_renderer,
            data_formatter=client_config.data_formatter
        )
        
        # Create state manager
        state_manager = StateManager(
            conversation_context=context,
            schema=client_config.schema,
            session_id=session_data['session_id'],
            patient_id=session_data['patient_id']
        )
        
        # Create transcript processor
        transcript_processor = TranscriptProcessor()
        
        # Create LLM context aggregator
        initial_prompt = context.render_prompt()
        llm_context = OpenAILLMContext(
            messages=[{"role": "system", "content": initial_prompt}],
            tools=PATIENT_TOOLS
        )
        # Disable tools initially for fast IVR classification
        llm_context.set_tools(NOT_GIVEN)
        context_aggregators = services['llm'].create_context_aggregator(llm_context)
        
        # Link state manager to context aggregators
        state_manager.set_context_aggregators(context_aggregators)
        
        # Get formatted data for prompt rendering
        formatted_data = client_config.data_formatter.format_patient_data(
            session_data['patient_data']
        )
        
        # Create IVR navigator
        ivr_goal = client_config.prompt_renderer.render_prompt(
            "ivr_navigation", "task", formatted_data
        ) or "Navigate to provider services for eligibility verification"
        
        # Configure IVRNavigator with optimized VAD parameters for <1s response
        ivr_navigator = IVRNavigator(
            llm=services['classifier_llm'],  # Fast classifier without tools
            ivr_prompt=ivr_goal,
            ivr_vad_params=VADParams(stop_secs=2.0)  # Longer wait for IVR menus
        )
        
        return {
            'context': context,
            'state_manager': state_manager,
            'transcript_processor': transcript_processor,
            'context_aggregators': context_aggregators,
            'ivr_navigator': ivr_navigator,
            'llm': services['llm']
        }
    
    @staticmethod
    def _assemble_pipeline(
        services: Dict[str, Any],
        components: Dict[str, Any]
    ) -> Pipeline:
        """Wire all components into Pipecat pipeline"""
        return Pipeline([
            services['transport'].input(),
            AudioResampler(target_sample_rate=16000),
            DropEmptyAudio(),
            services['stt'],
            components['transcript_processor'].user(),
            components['context_aggregators'].user(),
            components['ivr_navigator'],
            StateTagStripper(),
            SSMLCodeFormatter(),  # Apply SSML formatting before TTS
            services['tts'],
            components['transcript_processor'].assistant(),
            components['context_aggregators'].assistant(),
            services['transport'].output()
        ])