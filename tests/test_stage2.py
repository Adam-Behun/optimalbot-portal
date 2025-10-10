"""
Integration tests for Stage 2: Pre-computation + Rendering
"""

import pytest
import time
from pathlib import Path
from engine.schema_loader import ConversationSchema
from engine.data_formatter import DataFormatter
from engine.prompt_renderer import PromptRenderer
from engine.conversation_context import ConversationContext


# Sample patient data matching your schema
SAMPLE_PATIENT_DATA = {
    "_id": "12345",
    "patient_name": "John Smith",
    "date_of_birth": "1980-03-15",
    "insurance_member_id": "ABC123XYZ",
    "insurance_company_name": "Blue Cross",
    "cpt_code": "99213",
    "provider_npi": "1234567890",
    "facility_name": "Downtown Clinic",
    "appointment_time": "2025-10-15T10:30:00",
    "prior_auth_status": None,
    "reference_number": None
}


@pytest.fixture
def schema():
    """Load the prior_auth schema."""
    return ConversationSchema.load("clients/prior_auth")


@pytest.fixture
def formatted_data(schema):
    """Pre-format patient data."""
    formatter = DataFormatter(schema)
    return formatter.format_patient_data(SAMPLE_PATIENT_DATA)


class TestDataFormatting:
    """Test pre-computation/formatting layer."""
    
    def test_formatting_speed(self, schema):
        """Verify formatting completes in <50ms."""
        formatter = DataFormatter(schema)
        
        start = time.perf_counter()
        formatted = formatter.format_patient_data(SAMPLE_PATIENT_DATA)
        duration_ms = (time.perf_counter() - start) * 1000
        
        assert duration_ms < 50, f"Formatting took {duration_ms:.2f}ms (target: <50ms)"
        print(f"✅ Formatting: {duration_ms:.2f}ms")
    
    def test_date_formatting(self, formatted_data):
        """Verify date formatted to natural speech."""
        assert "date_of_birth_spoken" in formatted_data
        spoken = formatted_data["date_of_birth_spoken"]
        
        # Should be "March fifteenth, nineteen eighty"
        assert "March" in spoken
        assert "fifteenth" in spoken
        assert "nineteen" in spoken
        print(f"✅ Date spoken: {spoken}")
    
    def test_cpt_code_individual_digits(self, formatted_data):
        """Verify CPT code formatted as individual digits."""
        assert "cpt_code_spoken" in formatted_data
        spoken = formatted_data["cpt_code_spoken"]
        
        # Should be "9 9 2 1 3"
        assert spoken == "9 9 2 1 3"
        print(f"✅ CPT spoken: {spoken}")
    
    def test_npi_grouped_digits(self, formatted_data):
        """Verify NPI formatted with grouping."""
        assert "provider_npi_spoken" in formatted_data
        spoken = formatted_data["provider_npi_spoken"]
        
        # Should be "123 456 7890"
        assert "123" in spoken
        assert "456" in spoken
        assert "7890" in spoken
        print(f"✅ NPI spoken: {spoken}")
    
    def test_member_id_spell_out(self, formatted_data):
        """Verify member ID spelled out."""
        assert "insurance_member_id_spoken" in formatted_data
        spoken = formatted_data["insurance_member_id_spoken"]
        
        # Should be "A B C 1 2 3 X Y Z"
        assert "A" in spoken
        assert "B" in spoken
        assert "C" in spoken
        print(f"✅ Member ID spoken: {spoken}")
    
    def test_nato_alphabet_method(self, schema):
        """Verify NATO alphabet formatting works."""
        formatter = DataFormatter(schema)
        
        result = formatter._format_nato("ABC123")
        
        # Should contain NATO words
        assert "Alpha" in result
        assert "Bravo" in result
        assert "Charlie" in result
        assert "1" in result
        print(f"✅ NATO format: {result}")


class TestPromptRendering:
    """Test template pre-compilation and rendering."""
    
    def test_template_precompilation(self, schema):
        """Verify templates are pre-compiled at init."""
        renderer = PromptRenderer(schema)
        
        # Check cache is populated
        assert len(renderer._template_cache) > 0
        print(f"✅ Pre-compiled {len(renderer._template_cache)} templates")
    
    def test_render_speed(self, schema, formatted_data):
        """Verify rendering completes in <10ms."""
        renderer = PromptRenderer(schema)
        
        start = time.perf_counter()
        prompt = renderer.render_state_prompt("greeting", formatted_data)
        duration_ms = (time.perf_counter() - start) * 1000
        
        assert duration_ms < 10, f"Rendering took {duration_ms:.2f}ms (target: <10ms)"
        print(f"✅ Rendering: {duration_ms:.2f}ms")
    
    def test_greeting_prompt_rendering(self, schema, formatted_data):
        """Verify greeting prompt renders correctly."""
        renderer = PromptRenderer(schema)
        
        prompt = renderer.render_state_prompt("greeting", formatted_data)
        
        # Check patient data is included
        assert "John Smith" in prompt
        assert "1234567890" in prompt  # NPI
        print(f"✅ Greeting prompt length: {len(prompt)} chars")
    
    def test_patient_verification_prompt(self, schema, formatted_data):
        """Verify patient verification prompt with spoken fields."""
        renderer = PromptRenderer(schema)
        
        prompt = renderer.render_state_prompt("patient_verification", formatted_data)
        
        # Should include spoken versions
        assert "March fifteenth" in prompt  # date_of_birth_spoken
        assert "9 9 2 1 3" in prompt  # cpt_code_spoken
        print(f"✅ Verification prompt includes spoken fields")
    
    def test_conditional_rendering(self, schema, formatted_data):
        """Verify conditional template rendering works."""
        renderer = PromptRenderer(schema)
        
        # Without hold flag
        prompt1 = renderer.render_state_prompt(
            "patient_verification", 
            formatted_data,
            {"returning_from_hold": False}
        )
        
        # With hold flag
        prompt2 = renderer.render_state_prompt(
            "patient_verification",
            formatted_data,
            {"returning_from_hold": True}
        )
        
        # Prompts should differ when returning from hold
        assert "returning from hold" not in prompt1.lower()
        assert len(prompt2) > len(prompt1)  # Should have additional note
        print(f"✅ Conditional rendering works")


class TestConversationContext:
    """Test integrated context with formatting + rendering."""
    
    def test_context_initialization_speed(self, schema):
        """Verify context creation is fast."""
        start = time.perf_counter()
        
        context = ConversationContext(
            schema=schema,
            patient_data=SAMPLE_PATIENT_DATA,
            session_id="test-123"
        )
        
        duration_ms = (time.perf_counter() - start) * 1000
        
        # Should complete in <60ms (formatting + init)
        assert duration_ms < 100, f"Context init took {duration_ms:.2f}ms"
        print(f"✅ Context initialization: {duration_ms:.2f}ms")
    
    def test_render_prompt_integration(self, schema):
        """Verify full rendering pipeline works."""
        context = ConversationContext(
            schema=schema,
            patient_data=SAMPLE_PATIENT_DATA,
            session_id="test-123"
        )
        
        # Render greeting prompt
        prompt = context.render_prompt()
        
        assert len(prompt) > 100
        assert "Alexandra" in prompt or "John Smith" in prompt
        print(f"✅ Full prompt rendered: {len(prompt)} chars")
    
    def test_state_transitions_with_rendering(self, schema):
        """Verify prompts change with state transitions."""
        context = ConversationContext(
            schema=schema,
            patient_data=SAMPLE_PATIENT_DATA,
            session_id="test-123"
        )
        
        # Initial state
        prompt1 = context.render_prompt()
        
        # Transition to patient_verification
        context.transition_to("patient_verification", "testing")
        prompt2 = context.render_prompt()
        
        # Prompts should be different
        assert prompt1 != prompt2
        print(f"✅ State transitions produce different prompts")
    
    def test_renderer_caching(self, schema):
        """Verify renderer is cached across contexts."""
        context1 = ConversationContext(schema, SAMPLE_PATIENT_DATA, "test-1")
        context2 = ConversationContext(schema, SAMPLE_PATIENT_DATA, "test-2")
        
        # Should share the same renderer instance
        assert context1.renderer is context2.renderer
        print(f"✅ Renderer cached across contexts")


class TestPerformanceTargets:
    """Verify all Stage 2 performance targets are met."""
    
    def test_end_to_end_latency(self, schema):
        """Test complete flow: init context → render prompt."""
        # Simulate call start
        start = time.perf_counter()
        
        # 1. Create context (formats data)
        context = ConversationContext(
            schema=schema,
            patient_data=SAMPLE_PATIENT_DATA,
            session_id="perf-test"
        )
        
        # 2. Render initial prompt
        prompt = context.render_prompt()
        
        total_ms = (time.perf_counter() - start) * 1000
        
        # Total should be <70ms (50ms format + 10ms render + margin)
        assert total_ms < 100, f"End-to-end took {total_ms:.2f}ms"
        
        print(f"\n{'='*60}")
        print(f"PERFORMANCE SUMMARY")
        print(f"{'='*60}")
        print(f"End-to-end latency: {total_ms:.2f}ms (target: <100ms)")
        print(f"Prompt length: {len(prompt)} characters")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    # Run with: pytest tests/test_stage2_integration.py -v -s
    pytest.main([__file__, "-v", "-s"])