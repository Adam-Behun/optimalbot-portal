"""
Tests for IntentClassifier - Fast LLM-based classification with keyword fallback.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List
import time


# Mock schema classes (since we don't want to load actual YAML for unit tests)
class MockIntent:
    def __init__(self, name: str, keywords: List[str]):
        self.name = name
        self.keywords = keywords


class MockExample:
    def __init__(self, user_message: str, intent: str, reasoning: str = ""):
        self.user_message = user_message
        self.intent = intent
        self.reasoning = reasoning


class MockIntentClassificationConfig:
    def __init__(self):
        self.method = "llm_few_shot"
        self.max_classification_latency_ms = 100
        self.examples = [
            MockExample(
                "Hi, this is Sarah from Blue Cross. How can I help you?",
                "rep_greeted_caller",
                "Insurance rep greeting caller"
            ),
            MockExample(
                "Can you provide the patient's date of birth?",
                "rep_asked_for_patient_info",
                "Rep requesting verification info"
            ),
            MockExample(
                "What procedure are you calling about?",
                "rep_asked_for_authorization",
                "Moving to authorization check"
            ),
        ]


class MockConversationSchema:
    def __init__(self):
        self.intent_classification = MockIntentClassificationConfig()
        self.intents = [
            MockIntent("rep_greeted_caller", ["how can i help", "how may i assist"]),
            MockIntent("rep_asked_for_patient_info", ["date of birth", "member id", "patient name"]),
            MockIntent("rep_asked_for_authorization", ["what procedure", "cpt code", "authorization"]),
            MockIntent("rep_put_on_hold", ["hold please", "one moment", "let me check", "on hold"]),
            MockIntent("rep_returned_from_hold", ["thanks for holding", "i'm back"]),
            MockIntent("authorization_complete", ["approved", "denied", "reference number"]),
        ]


@pytest.fixture
def mock_schema():
    """Create mock schema for testing."""
    return MockConversationSchema()


@pytest.fixture
def mock_openai_response():
    """Create a mock OpenAI API response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "rep_greeted_caller"
    return mock_response


@pytest.fixture
def intent_classifier(mock_schema):
    """Create IntentClassifier instance with mock schema."""
    # Import here to avoid issues if the module doesn't exist yet
    from engine.intent_classifier import IntentClassifier
    
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
        classifier = IntentClassifier(mock_schema)
    
    return classifier


class TestIntentClassifierInit:
    """Test IntentClassifier initialization."""
    
    def test_initialization(self, intent_classifier):
        """Test classifier initializes correctly."""
        assert intent_classifier is not None
        assert len(intent_classifier._keyword_index) > 0
        assert intent_classifier._few_shot_prompt is not None
        assert len(intent_classifier._valid_intents) == 6
    
    def test_keyword_index_built(self, intent_classifier):
        """Test keyword index is built correctly."""
        # Should have lowercase keywords
        assert "how can i help" in intent_classifier._keyword_index
        assert "date of birth" in intent_classifier._keyword_index
        
        # Should map to correct intents
        assert intent_classifier._keyword_index["how can i help"] == "rep_greeted_caller"
        assert intent_classifier._keyword_index["date of birth"] == "rep_asked_for_patient_info"
    
    def test_few_shot_prompt_built(self, intent_classifier):
        """Test few-shot prompt contains examples."""
        prompt = intent_classifier._few_shot_prompt
        
        assert "insurance representative intent" in prompt.lower()
        assert "rep_greeted_caller" in prompt
        assert "Sarah from Blue Cross" in prompt
        assert "date of birth" in prompt


class TestKeywordClassification:
    """Test fast keyword-based classification."""
    
    @pytest.mark.asyncio
    async def test_simple_keyword_match(self, intent_classifier):
        """Test classification with clear keyword match."""
        # Mock LLM to force keyword fallback
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        intent, latency = await intent_classifier.classify(
            "Can you provide the patient's date of birth?"
        )
        
        assert intent == "rep_asked_for_patient_info"
        assert latency < 10  # Should be very fast
    
    @pytest.mark.asyncio
    async def test_keyword_case_insensitive(self, intent_classifier):
        """Test keyword matching is case-insensitive."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        intent, _ = await intent_classifier.classify(
            "WHAT PROCEDURE are you calling about?"
        )
        
        assert intent == "rep_asked_for_authorization"
    
    @pytest.mark.asyncio
    async def test_keyword_in_longer_sentence(self, intent_classifier):
        """Test keyword matching in longer sentences."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        intent, _ = await intent_classifier.classify(
            "Okay, before we proceed, hold please while I check the system."
        )
        
        assert intent == "rep_put_on_hold"
    
    @pytest.mark.asyncio
    async def test_no_keyword_match(self, intent_classifier):
        """Test unknown intent when no keywords match."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        intent, _ = await intent_classifier.classify(
            "This is a completely random message with no keywords."
        )
        
        assert intent == "unknown"
    
    @pytest.mark.asyncio
    async def test_empty_message(self, intent_classifier):
        """Test handling of empty messages."""
        intent, latency = await intent_classifier.classify("")
        
        assert intent == "unknown"
        assert latency >= 0


class TestLLMClassification:
    """Test LLM-based classification."""
    
    @pytest.mark.asyncio
    async def test_llm_classification_success(self, intent_classifier, mock_openai_response):
        """Test successful LLM classification."""
        # Mock successful LLM response
        mock_openai_response.choices[0].message.content = "rep_greeted_caller"
        intent_classifier.llm.chat.completions.create = AsyncMock(
            return_value=mock_openai_response
        )
        
        intent, latency = await intent_classifier.classify(
            "Hi there! This is Jennifer from Aetna. How may I assist you today?"
        )
        
        assert intent == "rep_greeted_caller"
        # Note: In real test this might be slow, but mocked should be fast
    
    @pytest.mark.asyncio
    async def test_llm_returns_invalid_intent(self, intent_classifier, mock_openai_response):
        """Test LLM returning an invalid intent falls back to keywords."""
        # Mock LLM returning invalid intent
        mock_openai_response.choices[0].message.content = "invalid_intent_name"
        intent_classifier.llm.chat.completions.create = AsyncMock(
            return_value=mock_openai_response
        )
        
        intent, _ = await intent_classifier.classify(
            "Can you provide date of birth?"
        )
        
        # Should fall back to keyword matching
        assert intent == "rep_asked_for_patient_info"
    
    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self, intent_classifier):
        """Test fallback to keywords when LLM fails."""
        # Mock LLM failure
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("API Error")
        )
        
        intent, _ = await intent_classifier.classify(
            "Let me check that authorization for you."
        )
        
        # Should fall back to keyword matching
        assert intent == "rep_asked_for_authorization"
        assert intent_classifier.stats["llm_failures"] > 0


class TestStatistics:
    """Test statistics tracking."""
    
    @pytest.mark.asyncio
    async def test_stats_initialization(self, intent_classifier):
        """Test statistics start at zero."""
        stats = intent_classifier.get_stats()
        
        assert stats["total_classifications"] == 0
        assert stats["llm_classifications"] == 0
        assert stats["keyword_fallbacks"] == 0
        assert stats["avg_latency_ms"] == 0.0
    
    @pytest.mark.asyncio
    async def test_stats_after_keyword_classification(self, intent_classifier):
        """Test statistics update after keyword classification."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        await intent_classifier.classify("Can you provide date of birth?")
        
        stats = intent_classifier.get_stats()
        assert stats["total_classifications"] == 1
        assert stats["keyword_fallbacks"] == 1
        assert stats["keyword_usage_percent"] == 100.0
        assert stats["avg_latency_ms"] > 0
    
    @pytest.mark.asyncio
    async def test_stats_after_llm_classification(self, intent_classifier, mock_openai_response):
        """Test statistics update after LLM classification."""
        mock_openai_response.choices[0].message.content = "rep_greeted_caller"
        intent_classifier.llm.chat.completions.create = AsyncMock(
            return_value=mock_openai_response
        )
        
        await intent_classifier.classify("Hi, how can I help?")
        
        stats = intent_classifier.get_stats()
        assert stats["total_classifications"] == 1
        assert stats["llm_classifications"] == 1
        assert stats["llm_usage_percent"] == 100.0
    
    @pytest.mark.asyncio
    async def test_stats_reset(self, intent_classifier):
        """Test statistics can be reset."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        await intent_classifier.classify("date of birth?")
        
        intent_classifier.reset_stats()
        stats = intent_classifier.get_stats()
        
        assert stats["total_classifications"] == 0
        assert stats["keyword_fallbacks"] == 0


class TestPerformance:
    """Test performance requirements."""
    
    @pytest.mark.asyncio
    async def test_keyword_classification_speed(self, intent_classifier):
        """Test keyword classification meets <10ms target."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        # Run multiple times to get average
        latencies = []
        for _ in range(10):
            _, latency = await intent_classifier.classify(
                "Can you provide the patient's date of birth?"
            )
            latencies.append(latency)
        
        avg_latency = sum(latencies) / len(latencies)
        
        # Keyword fallback should be very fast
        assert avg_latency < 10, f"Average latency {avg_latency:.1f}ms exceeds 10ms target"
    
    @pytest.mark.asyncio
    async def test_multiple_classifications(self, intent_classifier):
        """Test multiple classifications work correctly."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        messages = [
            "How can I help you?",
            "Can you provide date of birth?",
            "What procedure is this for?",
            "Let me put you on hold.",
            "The authorization is approved."
        ]
        
        for msg in messages:
            intent, latency = await intent_classifier.classify(msg)
            assert intent != "unknown"
            assert latency >= 0
        
        stats = intent_classifier.get_stats()
        assert stats["total_classifications"] == 5


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    @pytest.mark.asyncio
    async def test_very_long_message(self, intent_classifier):
        """Test handling of very long messages."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        long_message = "I need to " + "blah " * 1000 + "check the authorization"
        intent, _ = await intent_classifier.classify(long_message)
        
        assert intent == "rep_asked_for_authorization"
    
    @pytest.mark.asyncio
    async def test_whitespace_only_message(self, intent_classifier):
        """Test handling of whitespace-only messages."""
        intent, _ = await intent_classifier.classify("   \n\t   ")
        
        assert intent == "unknown"
    
    @pytest.mark.asyncio
    async def test_special_characters(self, intent_classifier):
        """Test handling of special characters."""
        intent_classifier.llm.chat.completions.create = AsyncMock(
            side_effect=Exception("Force keyword fallback")
        )
        
        intent, _ = await intent_classifier.classify(
            "Can you provide the patient's DOB? @#$%"
        )
        
        # Should still match "date of birth" variant or unknown
        assert intent in ["rep_asked_for_patient_info", "unknown"]


# Integration-style test (requires actual schema file)
class TestWithRealSchema:
    """Tests that require actual schema.yaml file."""
    
    @pytest.mark.integration
    @pytest.mark.skipif(
        True,  # Skip by default, run with: pytest -m integration
        reason="Requires actual schema.yaml file"
    )
    @pytest.mark.asyncio
    async def test_with_real_schema(self):
        """Test with actual schema.yaml file."""
        from engine import ConversationSchema
        from engine.intent_classifier import IntentClassifier
        
        schema = ConversationSchema.load("clients/prior_auth")
        classifier = IntentClassifier(schema)
        
        intent, latency = await classifier.classify(
            "Can you provide the patient's date of birth?"
        )
        
        assert intent in ["rep_asked_for_patient_info", "unknown"]
        assert latency >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])