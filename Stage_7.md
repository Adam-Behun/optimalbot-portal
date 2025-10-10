Technical Refactoring Plan: Universal Voice Engine + Pluggable Conversation Schemas
Version: 2.0 - Optimized for Production Voice AI
Critical Design Principles (Applied Throughout)
Latency-First Design

Pre-compute everything possible at initialization: Format pronunciations, render static prompts, cache templates
Minimize runtime overhead: Target <50ms total schema overhead per conversation turn
Benchmark everything: Every stage must measure and report latency impact

Streaming-Native Architecture

Schema components must support incremental prompt building
State transitions don't block response streaming
LLM can start responding before full prompt assembly

Smart State Management

Schema parsed once at app startup, cached in memory
Per-call state machines are lightweight (no schema re-parsing)
Minimal object creation in hot path

STAGE 7: Testing, Documentation & Optimization
Context for LLM Expert
You are creating comprehensive tests and documentation for the schema system, plus identifying any remaining performance bottlenecks.
Files You Need to Request
Please provide:
1. All engine/ modules
2. clients/prior_auth/ schema files
3. Current test setup (if any)
4. Any existing documentation structure
Implementation Requirements
7.1 Performance Test Suite
File: tests/test_performance.py
python"""
Performance tests - Ensure schema system meets latency targets.
"""

import pytest
import time
from engine.schema_loader import ConversationSchema
from engine.precompute import DataPrecomputer
from engine.prompt_renderer import PromptRenderer
from engine.intent_classifier import IntentClassifier
from engine.state_machine import StateManagerFactory


@pytest.fixture
def schema():
    return ConversationSchema.load("clients/prior_auth")


@pytest.fixture
def components(schema):
    """Create all components"""
    renderer = PromptRenderer(schema)
    precomputer = DataPrecomputer(schema)
    # Mock LLM client for testing
    classifier = IntentClassifier(schema, MockLLMClient())
    factory = StateManagerFactory(schema, classifier)
    return schema, renderer, precomputer, classifier, factory


def test_schema_load_performance():
    """Schema load must be <500ms"""
    start = time.perf_counter()
    schema = ConversationSchema.load("clients/prior_auth")
    load_time_ms = (time.perf_counter() - start) * 1000
    
    assert load_time_ms < 500, f"Schema load took {load_time_ms:.2f}ms (target: <500ms)"


def test_precompute_performance(components):
    """Pre-computation must be <50ms"""
    _, _, precomputer, _, _ = components
    
    patient_data = {
        "patient_name": "John Smith",
        "date_of_birth": "1980-01-15",
        "insurance_member_id": "ABC123",
        "cpt_code": "99213",
        "provider_npi": "1234567890"
    }
    
    start = time.perf_counter()
    precomputed = precomputer.precompute_all(patient_data)
    compute_time_ms = (time.perf_counter() - start) * 1000
    
    assert compute_time_ms < 50, f"Precompute took {compute_time_ms:.2f}ms (target: <50ms)"
    assert "date_of_birth_spoken" in precomputed
    assert "insurance_member_id_spoken" in precomputed


def test_prompt_render_performance(components):
    """Prompt rendering must be <10ms with cached templates"""
    _, renderer, _, _, _ = components
    
    precomputed_data = {
        "patient_name": "John Smith",
        "date_of_birth_spoken": "January fifteenth, nineteen eighty"
    }
    
    # First render (may compile template)
    renderer.render_state_prompt("greeting", precomputed_data)
    
    # Second render should be fast (cached template)
    start = time.perf_counter()
    prompt = renderer.render_state_prompt("greeting", precomputed_data)
    render_time_ms = (time.perf_counter() - start) * 1000
    
    assert render_time_ms < 10, f"Render took {render_time_ms:.2f}ms (target: <10ms)"
    assert len(prompt) > 0


def test_state_machine_performance(components):
    """State machine operations must be fast"""
    _, _, _, _, factory = components
    
    # Creating state should be <5ms
    start = time.perf_counter()
    state = factory.create_state("test-session-1")
    create_time_ms = (time.perf_counter() - start) * 1000
    
    assert create_time_ms < 5, f"State creation took {create_time_ms:.2f}ms"
    
    # State transition should be <10ms
    start = time.perf_counter()
    state.transition_to("patient_verification")
    transition_time_ms = (time.perf_counter() - start) * 1000
    
    assert transition_time_ms < 10, f"Transition took {transition_time_ms:.2f}ms"


def test_end_to_end_latency(components):
    """Complete flow from precompute to render"""
    schema, renderer, precomputer, _, factory = components
    
    patient_data = {
        "patient_name": "John Smith",
        "date_of_birth": "1980-01-15",
        "insurance_member_id": "ABC123",
        "insurance_company_name": "Blue Cross",
        "cpt_code": "99213",
        "provider_npi": "1234567890"
    }
    
    total_start = time.perf_counter()
    
    # Precompute
    precomputed = precomputer.precompute_all(patient_data)
    
    # Create state
    state = factory.create_state("test-session-2")
    
    # Render initial prompt
    prompt = renderer.render_state_prompt(state.current_state, precomputed)
    
    total_time_ms = (time.perf_counter() - total_start) * 1000
    
    # Total overhead should be <100ms for call initialization
    assert total_time_ms < 100, (
        f"End-to-end initialization took {total_time_ms:.2f}ms (target: <100ms)"
    )
    
    print(f"\n✓ End-to-end initialization: {total_time_ms:.2f}ms")
7.2 Client Onboarding Guide
File: clients/prior_auth/README.md
markdown# Prior Authorization Voice Agent Configuration

This directory contains all configuration for the healthcare prior authorization voice agent.

## Quick Start

### Editing Prompts
1. Open `prompts.yaml`
2. Find the state you want to modify (e.g., `greeting`)
3. Edit the text between the `|` markers
4. Variables in `{{brackets}}` are automatically filled
5. Save and restart the application

### Testing Changes
```bash
# View rendered prompt
curl http://localhost:8000/schema/test-render/greeting

# Test pre-computation
curl http://localhost:8000/schema/test-precompute

# Check performance
curl http://localhost:8000/schema/info
Performance Targets
The schema system is optimized for low latency:

Schema load: <500ms at startup
Pre-computation: <50ms per call
Prompt rendering: <10ms per state
Total overhead: <100ms call initialization

File Structure
clients/prior_auth/
├── schema.yaml          # Conversation structure
├── prompts.yaml         # What the agent says
├── functions.py         # Custom business logic (optional)
└── README.md           # This file
Common Customizations
Change Agent's Name
In schema.yaml:
yamlvoice:
  persona:
    name: "YourNameHere"  # Change this
Add New State

In schema.yaml, add to states.definitions:

yaml- name: "new_state_name"
  description: "What this state does"
  prompts_ref: "new_state_name"
  transitions:
    - trigger: "some_intent"
      next_state: "next_state"

In prompts.yaml, add prompts:

yamlprompts:
  new_state_name:
    task: |
      What the agent should do in this state
Modify Data Pronunciation
In schema.yaml under preformat_rules:
yamlyour_field_name:
  format: "nato_alphabet"  # or "natural_speech", "individual_digits"
Troubleshooting
Slow Performance
Check performance metrics:
bashcurl http://localhost:8000/schema/info
Look for:

init_time_acceptable: true
total_init_ms < 500

Invalid Schema
The application won't start if schema is invalid. Check logs for:

Missing required fields
Invalid state transitions
Missing prompt references

Intent Not Recognized

Check intent_classification.examples in schema.yaml
Add more examples for your use case
Verify keywords in intents section


### Success Criteria
- [ ] All performance tests pass
- [ ] Documentation complete and clear
- [ ] Performance report identifies any >50ms operations
- [ ] Client can modify prompts without developer help

### What to Ask the Engineer
Before implementing, please provide:

All engine/ modules for testing
Your testing framework preferences
Documentation standards/templates
Where you want performance reports saved

I'll create comprehensive tests and docs that match your standards.