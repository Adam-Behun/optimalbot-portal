"""
Tests for Schema Loader - Validates schema loading, caching, and validation.
"""

import pytest
from pathlib import Path
import tempfile
import shutil
import yaml
from engine.schema_loader import ConversationSchema


@pytest.fixture
def temp_schema_dir():
    """Create a temporary directory for test schemas"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def valid_schema_yaml():
    """Valid schema configuration"""
    return {
        "conversation": {
            "name": "Test Conversation",
            "version": "1.0.0",
            "client_id": "test_client",
            "precompute_strategy": {
                "pronunciation_formatters": True,
                "static_prompts": True,
                "template_cache": True
            }
        },
        "voice": {
            "persona": {
                "name": "Alex",
                "role": "Assistant",
                "company": "Test Company"
            },
            "speaking_style": {
                "tone": "professional",
                "pace": "moderate",
                "max_words_per_response": 30
            }
        },
        "data_schema": {
            "entity_name": "Patient",
            "required_fields": ["name", "dob"],
            "optional_fields": ["phone"],
            "output_fields": ["status"],
            "preformat_rules": {
                "dob": {
                    "format": "natural_speech"
                }
            }
        },
        "states": {
            "initial_state": "greeting",
            "definitions": [
                {
                    "name": "greeting",
                    "description": "Initial greeting",
                    "respond_immediately": False,
                    "prompts_ref": "greeting",
                    "streaming": {"enabled": True},
                    "transitions": [
                        {"trigger": "info_requested", "next_state": "info_gathering"}
                    ]
                },
                {
                    "name": "info_gathering",
                    "description": "Gather information",
                    "respond_immediately": False,
                    "prompts_ref": "info_gathering",
                    "data_access": ["name", "dob"],
                    "transitions": [
                        {"trigger": "complete", "next_state": "closing"}
                    ]
                },
                {
                    "name": "closing",
                    "description": "End conversation",
                    "respond_immediately": True,
                    "prompts_ref": "closing",
                    "transitions": [],
                    "post_actions": [{"type": "end_conversation"}]
                }
            ]
        },
        "intent_classification": {
            "method": "llm_few_shot",
            "max_classification_latency_ms": 100,
            "examples": [
                {
                    "user_message": "Can I get your name?",
                    "intent": "info_requested",
                    "reasoning": "Requesting information"
                }
            ]
        },
        "intents": [
            {
                "name": "info_requested",
                "description": "User requests information",
                "keywords": ["name", "info"],
                "patterns": ["can i get", "what is"]
            }
        ],
        "observability": {
            "track_latency": {
                "schema_load_time": True,
                "precompute_time": True,
                "state_transition_time": True,
                "prompt_render_time": True
            },
            "events": ["state_transitions", "function_calls"]
        }
    }


@pytest.fixture
def valid_prompts_yaml():
    """Valid prompts configuration"""
    return {
        "prompts": {
            "greeting": {
                "system": "You are a helpful assistant.",
                "task": "Greet the user warmly."
            },
            "info_gathering": {
                "task": "Ask for patient information: {{ name }}, {{ dob }}"
            },
            "closing": {
                "task": "Thank the user and say goodbye."
            }
        }
    }


def create_test_schema(temp_dir, schema_data, prompts_data):
    """Helper to create schema files in temp directory"""
    schema_path = Path(temp_dir)
    
    # Write schema.yaml
    with open(schema_path / 'schema.yaml', 'w') as f:
        yaml.dump(schema_data, f)
    
    # Write prompts.yaml
    with open(schema_path / 'prompts.yaml', 'w') as f:
        yaml.dump(prompts_data, f)
    
    return str(schema_path)


class TestSchemaLoading:
    """Test schema loading functionality"""
    
    def test_load_valid_schema(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test loading a valid schema successfully"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        
        schema = ConversationSchema.load(schema_path)
        
        assert schema is not None
        assert schema.conversation.name == "Test Conversation"
        assert schema.conversation.version == "1.0.0"
        assert schema.conversation.client_id == "test_client"
    
    def test_load_time_tracking(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test that load time is tracked"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        
        schema = ConversationSchema.load(schema_path)
        
        assert schema._load_time_ms > 0
        assert schema._load_time_ms < 500, "Schema load should be under 500ms"
    
    def test_load_missing_directory(self):
        """Test loading from non-existent directory"""
        with pytest.raises(FileNotFoundError, match="Schema directory not found"):
            ConversationSchema.load("/nonexistent/path")
    
    def test_load_missing_schema_file(self, temp_schema_dir, valid_prompts_yaml):
        """Test loading when schema.yaml is missing"""
        # Only create prompts.yaml
        with open(Path(temp_schema_dir) / 'prompts.yaml', 'w') as f:
            yaml.dump(valid_prompts_yaml, f)
        
        with pytest.raises(FileNotFoundError, match="schema.yaml not found"):
            ConversationSchema.load(temp_schema_dir)
    
    def test_load_missing_prompts_file(self, temp_schema_dir, valid_schema_yaml):
        """Test loading when prompts.yaml is missing"""
        # Only create schema.yaml
        with open(Path(temp_schema_dir) / 'schema.yaml', 'w') as f:
            yaml.dump(valid_schema_yaml, f)
        
        with pytest.raises(FileNotFoundError, match="prompts.yaml not found"):
            ConversationSchema.load(temp_schema_dir)


class TestSchemaValidation:
    """Test schema validation logic"""
    
    def test_validate_invalid_state_transition(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test that invalid state transitions are caught"""
        # Modify schema to have invalid transition
        invalid_schema = valid_schema_yaml.copy()
        invalid_schema['states']['definitions'][0]['transitions'] = [
            {"trigger": "bad_trigger", "next_state": "nonexistent_state"}
        ]
        
        schema_path = create_test_schema(temp_schema_dir, invalid_schema, valid_prompts_yaml)
        
        with pytest.raises(ValueError, match="non-existent state"):
            ConversationSchema.load(schema_path)
    
    def test_validate_missing_required_fields(self, temp_schema_dir, valid_prompts_yaml):
        """Test that missing required fields are caught"""
        # Schema missing required 'conversation' field
        invalid_schema = {
            "voice": {
                "persona": {"name": "Test", "role": "Test", "company": "Test"},
                "speaking_style": {"tone": "test", "pace": "test", "max_words_per_response": 30}
            }
        }
        
        schema_path = create_test_schema(temp_schema_dir, invalid_schema, valid_prompts_yaml)
        
        with pytest.raises(ValueError):
            ConversationSchema.load(schema_path)


class TestSchemaAccessors:
    """Test schema accessor methods"""
    
    def test_get_initial_state(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test getting initial state"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        initial_state = schema.get_initial_state()
        
        assert initial_state == "greeting"
    
    def test_get_state_by_name(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test getting state definition by name"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        state = schema.get_state("info_gathering")
        
        assert state.name == "info_gathering"
        assert state.description == "Gather information"
        assert "name" in state.data_access
        assert "dob" in state.data_access
    
    def test_get_nonexistent_state(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test getting non-existent state raises error"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        with pytest.raises(ValueError, match="State 'invalid' not found"):
            schema.get_state("invalid")
    
    def test_get_prompts_for_state(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test getting prompts for a state"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        prompts = schema.get_prompts_for_state("greeting")
        
        assert "system" in prompts
        assert "task" in prompts
        assert prompts["system"] == "You are a helpful assistant."
    
    def test_get_prompts_for_invalid_ref(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test getting prompts with invalid reference"""
        # Modify schema to reference non-existent prompt
        invalid_schema = valid_schema_yaml.copy()
        invalid_schema['states']['definitions'][0]['prompts_ref'] = 'nonexistent'
        
        schema_path = create_test_schema(temp_schema_dir, invalid_schema, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        with pytest.raises(ValueError, match="Prompts reference 'nonexistent' not found"):
            schema.get_prompts_for_state("greeting")


class TestVoiceConfiguration:
    """Test voice configuration parsing"""
    
    def test_voice_persona_loaded(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test voice persona is correctly loaded"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert schema.voice.persona.name == "Alex"
        assert schema.voice.persona.role == "Assistant"
        assert schema.voice.persona.company == "Test Company"
    
    def test_speaking_style_loaded(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test speaking style is correctly loaded"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert schema.voice.speaking_style.tone == "professional"
        assert schema.voice.speaking_style.pace == "moderate"
        assert schema.voice.speaking_style.max_words_per_response == 30


class TestDataSchema:
    """Test data schema configuration"""
    
    def test_required_fields_loaded(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test required fields are loaded"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert "name" in schema.data_schema.required_fields
        assert "dob" in schema.data_schema.required_fields
    
    def test_preformat_rules_loaded(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test preformat rules are loaded"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert "dob" in schema.data_schema.preformat_rules
        assert schema.data_schema.preformat_rules["dob"].format == "natural_speech"


class TestIntentConfiguration:
    """Test intent classification configuration"""
    
    def test_intent_examples_loaded(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test intent classification examples are loaded"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert len(schema.intent_classification.examples) > 0
        example = schema.intent_classification.examples[0]
        assert example.user_message == "Can I get your name?"
        assert example.intent == "info_requested"
    
    def test_intent_definitions_loaded(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test intent definitions are loaded"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert len(schema.intents) > 0
        intent = schema.intents[0]
        assert intent.name == "info_requested"
        assert "name" in intent.keywords


class TestObservability:
    """Test observability configuration"""
    
    def test_latency_tracking_configured(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test latency tracking configuration"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert schema.observability.track_latency["schema_load_time"] == True
        assert schema.observability.track_latency["precompute_time"] == True
    
    def test_events_configured(self, temp_schema_dir, valid_schema_yaml, valid_prompts_yaml):
        """Test event tracking configuration"""
        schema_path = create_test_schema(temp_schema_dir, valid_schema_yaml, valid_prompts_yaml)
        schema = ConversationSchema.load(schema_path)
        
        assert "state_transitions" in schema.observability.events
        assert "function_calls" in schema.observability.events