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
from pipeline.audio_processors import AudioResampler, DropEmptyAudio, StateTagStripper, CodeFormatter
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

        # Create LLM switcher (manages classifier_llm and main llm)
        llm_switcher, classifier_llm, main_llm = ServiceFactory.create_llm_switcher(services_config['services'])

        # Create services
        services = {
            'stt': ServiceFactory.create_stt(services_config['services']['stt']),
            'tts': ServiceFactory.create_tts(services_config['services']['tts']),
            'llm_switcher': llm_switcher,
            'classifier_llm': classifier_llm,  # Keep reference for IVRNavigator
            'main_llm': main_llm,  # Keep reference for switching
            'transport': ServiceFactory.create_transport(
                services_config['services']['transport'],
                room_config['room_url'],
                room_config['room_token'],
                room_config['room_name']
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
        
        # Create LLM context aggregator using main_llm
        # Note: Context aggregator routes messages through IVRNavigator → LLMSwitcher → active LLM
        initial_prompt = context.render_prompt()
        llm_context = OpenAILLMContext(
            messages=[{"role": "system", "content": initial_prompt}],
            tools=NOT_GIVEN  # Start with no tools - classifier_llm is active initially
        )
        context_aggregators = services['main_llm'].create_context_aggregator(llm_context)
        
        # Link state manager to context aggregators
        state_manager.set_context_aggregators(context_aggregators)
        
        # Get formatted data for prompt rendering
        formatted_data = client_config.data_formatter.format_patient_data(
            session_data['patient_data']
        )
        
        # Render IVR classifier prompt from YAML
        ivr_classifier_prompt = client_config.prompt_renderer.render_prompt(
            "ivr_classifier", "system", {}
        )

        # Create IVR navigator
        ivr_goal = client_config.prompt_renderer.render_prompt(
            "ivr_navigation", "task", formatted_data
        ) or "Navigate to provider services for eligibility verification"

        # Configure IVRNavigator with LLM switcher (starts with classifier_llm active)
        ivr_navigator = IVRNavigator(
            llm=services['llm_switcher'],  # LLM switcher (classifier active initially)
            ivr_prompt=ivr_goal,
            ivr_vad_params=VADParams(stop_secs=2.0)  # Longer wait for IVR menus
        )

        # Override the classifier prompt with our custom one from YAML
        if ivr_classifier_prompt:
            ivr_navigator._classifier_prompt = ivr_classifier_prompt
            ivr_navigator._ivr_processor._classifier_prompt = ivr_classifier_prompt
        
        return {
            'context': context,
            'state_manager': state_manager,
            'transcript_processor': transcript_processor,
            'context_aggregators': context_aggregators,
            'ivr_navigator': ivr_navigator,
            'llm_switcher': services['llm_switcher'],
            'main_llm': services['main_llm']
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
            CodeFormatter(),  # Format hyphenated codes before TTS
            services['tts'],
            components['transcript_processor'].assistant(),
            components['context_aggregators'].assistant(),
            services['transport'].output()
        ])