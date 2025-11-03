# Implementation v0.2 - LLM Switching & State Management Refactor

## Executive Summary

This document outlines the architectural improvements for the voice AI system's LLM switching and state management implementation. The original plan (v0.1) had several critical weaknesses that could lead to reliability issues, maintenance challenges, and potential race conditions. This refined plan (v0.2) addresses these concerns through proper separation of concerns, configuration-driven design, and robust error handling.

## Original Plan (v0.1) - Critical Weaknesses

### 1. **Improper LLM Switching Architecture**

**Problem:**
- Direct use of `ManuallySwitchServiceFrame` in `StateManager` creates tight coupling
- Hardcoded `STATE_LLM_MAP` dictionary violates configuration-driven design principles
- No error handling for failed LLM switches
- No fallback mechanism if switching fails

**Impact:**
- Difficult to modify LLM assignments without code changes
- System could crash if LLM switch fails
- Testing requires complex mocking

### 2. **StateManager Overload - Single Responsibility Violation**

**Problem:**
```python
# StateManager handling too many responsibilities:
- State transitions
- LLM switching
- Tool management
- Context clearing
- Message frame updates
```

**Impact:**
- Single class becomes unmaintainable
- Changes to one concern affect all others
- Unit testing becomes nearly impossible
- High cognitive load for developers

### 3. **Aggressive Context Management**

**Problem:**
- Clearing entire conversation history when entering greeting/call_classifier states
- No distinction between IVR context (should clear) and conversation context (should preserve)
- Loss of valuable metadata (patient ID, session ID, authorization status)

**Impact:**
- Loss of conversation continuity
- Potential compliance issues (losing call audit trail)
- Poor user experience if context needed later

### 4. **Race Condition Vulnerabilities**

**Problem:**
- No mutex/locking for concurrent state transitions
- Multiple events could trigger simultaneous transitions
- LLM switching and state transitions aren't atomic operations

**Example Scenario:**
```python
# These could happen simultaneously:
Event 1: IVR detected → transition to ivr_navigation
Event 2: Human detected → transition to greeting
# Result: Undefined state
```

**Impact:**
- Unpredictable system behavior
- Potential deadlocks or infinite loops
- Difficult to debug production issues

### 5. **Missing Error Recovery**

**Problem:**
- No fallback if LLM switch fails
- No retry logic for state transitions
- No handling of partial state transitions
- No rollback mechanism

**Impact:**
- System hangs on failures
- Calls drop unexpectedly
- Poor reliability metrics

### 6. **Testing Challenges**

**Problem:**
- Monolithic components difficult to mock
- State transitions tied to implementation details
- No clear boundaries for unit tests

**Impact:**
- Low test coverage
- Brittle tests that break with refactoring
- Difficulty catching edge cases

## Refined Implementation Plan (v0.2)

### Core Architecture Principles

1. **Single Responsibility**: Each component has one clear job
2. **Configuration over Code**: Move decisions to YAML when possible
3. **Fail-Safe Design**: Always have fallback paths
4. **Atomic Operations**: State transitions either fully succeed or fully fail
5. **Observable System**: Emit events for monitoring and debugging
6. **Testable Components**: Each piece can be tested in isolation

### Phase 1: Core Infrastructure Components

#### 1.1 LLMManager Class (`core/llm_manager.py`)

**Purpose:** Encapsulate all LLM switching logic with proper error handling

```python
class LLMManager:
    """Manages LLM switching based on state configuration"""

    def __init__(self, llm_switcher, config):
        self.llm_switcher = llm_switcher
        self.state_llm_config = config.get('state_llm_mapping', {})
        self.default_llm = config.get('default_llm', 'main')
        self._switch_lock = asyncio.Lock()
        self._current_state = None

    async def switch_for_state(self, state_name: str) -> bool:
        """Thread-safe LLM switching with error handling"""
        async with self._switch_lock:
            try:
                llm_type = self.state_llm_config.get(state_name, self.default_llm)
                target_llm = self._get_llm_by_type(llm_type)

                if self.llm_switcher.active_llm != target_llm:
                    # Add telemetry
                    span = trace.get_current_span()
                    span.set_attribute("llm.switch.from", type(self.llm_switcher.active_llm).__name__)
                    span.set_attribute("llm.switch.to", type(target_llm).__name__)

                    await self._perform_switch(target_llm)
                    self._current_state = state_name
                    logger.info(f"✅ LLM switched for state {state_name}")
                    return True

            except Exception as e:
                logger.error(f"❌ LLM switch failed: {e}")
                # Fallback to default LLM
                await self._fallback_to_default()
                return False

    async def _fallback_to_default(self):
        """Fallback mechanism for failed switches"""
        try:
            default = self._get_llm_by_type(self.default_llm)
            await self._perform_switch(default)
            logger.warning(f"⚠️ Fallback to default LLM: {self.default_llm}")
        except Exception as e:
            logger.critical(f"🔴 Critical: Fallback failed: {e}")
            raise SystemError("LLM system unavailable")
```

**Benefits:**
- Centralized switching logic
- Thread-safe operations
- Built-in fallback mechanism
- Observable through telemetry
- Testable in isolation

#### 1.2 ContextManager Class (`core/context_manager.py`)

**Purpose:** Intelligent context management with selective clearing

```python
class ContextManager:
    """Manages conversation context with selective clearing"""

    def __init__(self):
        self.ivr_context = []          # IVR-specific messages to clear
        self.conversation_context = []  # Human conversation to preserve
        self.system_context = {}        # System prompts by state
        self.metadata = {}              # Critical data to always preserve
        self._lock = asyncio.Lock()

    async def prepare_context_for_state(self, state_name: str, state_config: dict) -> List[Dict]:
        """Build appropriate context for state transition"""
        async with self._lock:
            if state_config.get('clear_context', False):
                return await self._build_fresh_context(state_name, state_config)
            else:
                return await self._build_continuous_context(state_name)

    async def _build_fresh_context(self, state_name: str, config: dict) -> List[Dict]:
        """Fresh context with metadata preservation"""
        messages = [
            {"role": "system", "content": self.system_context[state_name]}
        ]

        # Preserve critical metadata
        if config.get('preserve_metadata', True):
            messages.extend(self._get_preserved_metadata())

        # Clear IVR context but keep conversation summary if needed
        if config.get('include_summary', False):
            messages.append(self._generate_conversation_summary())

        # Clear IVR context
        self.ivr_context.clear()
        logger.info(f"🧹 Cleared IVR context for {state_name}")

        return messages

    def _get_preserved_metadata(self) -> List[Dict]:
        """Return critical metadata that should never be lost"""
        return [
            {
                "role": "system",
                "content": f"Session: {self.metadata.get('session_id')}\n" +
                          f"Patient ID: {self.metadata.get('patient_id')}\n" +
                          f"Auth Status: {self.metadata.get('auth_status', 'Pending')}"
            }
        ]
```

**Benefits:**
- Selective context clearing
- Metadata preservation
- Thread-safe operations
- Configurable per state
- Maintains conversation continuity

#### 1.3 StateTransitionManager Class (`core/state_transition.py`)

**Purpose:** Handle atomic state transitions with rollback capability

```python
class StateTransitionManager:
    """Handles atomic state transitions with rollback"""

    def __init__(self, schema, llm_manager, context_manager, tool_manager):
        self.schema = schema
        self.llm_manager = llm_manager
        self.context_manager = context_manager
        self.tool_manager = tool_manager
        self._transition_lock = asyncio.Lock()
        self._current_state = schema.initial_state
        self._transition_history = []

    async def transition(self, to_state: str, reason: str) -> TransitionResult:
        """Atomic transition with rollback on failure"""

        from_state = self._current_state
        transition = StateTransition(from_state, to_state, reason, timestamp=time.time())

        async with self._transition_lock:
            # Create checkpoint for rollback
            checkpoint = self._create_checkpoint()

            try:
                # 1. Validate transition
                if not self._validate_transition(from_state, to_state):
                    raise InvalidTransitionError(f"Invalid: {from_state} → {to_state}")

                # 2. Prepare new context
                state_config = self.schema.get_state_config(to_state)
                new_context = await self.context_manager.prepare_context_for_state(
                    to_state,
                    state_config
                )

                # 3. Switch LLM if needed
                llm_switched = await self.llm_manager.switch_for_state(to_state)
                if not llm_switched:
                    logger.warning(f"⚠️ LLM switch failed, using fallback")

                # 4. Update tools based on state
                await self.tool_manager.configure_for_state(to_state, state_config)

                # 5. Update conversation context
                await self._update_conversation_context(new_context)

                # 6. Commit state change
                self._current_state = to_state
                self._transition_history.append(transition)

                # Emit transition event
                await self._emit_transition_event(transition)

                logger.info(f"✅ Transition complete: {from_state} → {to_state}")
                return TransitionResult(success=True, transition=transition)

            except Exception as e:
                logger.error(f"❌ Transition failed: {e}")
                # Rollback to checkpoint
                await self._rollback_to_checkpoint(checkpoint)
                return TransitionResult(
                    success=False,
                    error=str(e),
                    from_state=from_state,
                    attempted_state=to_state
                )
```

**Benefits:**
- Atomic transitions
- Rollback capability
- Clear separation of concerns
- Comprehensive error handling
- Transition history tracking

### Phase 2: Configuration Schema Updates

#### 2.1 Enhanced State Configuration (`clients/prior_auth/schema.yaml`)

```yaml
# LLM Configuration Section
llm_configuration:
  models:
    classifier:
      type: "gpt-4o-mini"        # Fast, cheap model
      provider: "openai"
      temperature: 0.1
      max_tokens: 150

    main:
      type: "gpt-4o"              # Powerful model
      provider: "openai"
      temperature: 0.3
      max_tokens: 500

  state_mapping:
    call_classifier: "classifier"
    greeting: "classifier"
    ivr_navigation: "main"
    verification: "main"
    closing: "main"

  fallback_model: "main"
  switch_timeout_ms: 1000
  retry_attempts: 2

# Enhanced State Definitions
states:
  initial_state: "call_classifier"

  definitions:
    - name: "call_classifier"
      description: "Classify incoming audio as IVR or human"
      prompts_ref: "call_classifier"

      # LLM Configuration
      llm_model: "classifier"
      tools_enabled: false

      # Context Management
      clear_context: true
      preserve_metadata: true

      # Transitions
      allowed_transitions: ["ivr_navigation", "greeting"]
      llm_directed: false

    - name: "greeting"
      description: "Natural greeting with human"
      prompts_ref: "greeting"

      # LLM Configuration
      llm_model: "classifier"
      tools_enabled: false

      # Context Management
      clear_context: true           # Clear IVR history
      preserve_metadata: true        # Keep session/patient info
      include_summary: false         # No need for IVR summary

      # Data Access
      data_access:
        - patient_name               # Only patient name in greeting

      # Transitions
      allowed_transitions: ["verification"]
      llm_directed: true            # LLM controls when to transition

    - name: "verification"
      description: "Verify insurance and get authorization"
      prompts_ref: "verification"

      # LLM Configuration
      llm_model: "main"
      tools_enabled: true

      # Context Management
      clear_context: false          # Keep conversation history
      preserve_metadata: true

      # Data Access
      data_access:
        - patient_name
        - date_of_birth
        - insurance_member_id
        - cpt_code
        - provider_npi
        - patient_id

      # Functions
      functions:
        - update_prior_auth_status
        - dial_supervisor

      # Transitions
      allowed_transitions: ["closing", "call_classifier"]
      llm_directed: true
      transfer_detection: true      # Monitor for transfer mentions
```

### Phase 3: Pipeline Assembly Updates

#### 3.1 Pipeline Factory Integration (`pipeline/pipeline_factory.py`)

```python
@staticmethod
def _create_conversation_components(client_config, session_data, services):
    """Create conversation components with proper separation of concerns"""

    # 1. Create core managers
    llm_manager = LLMManager(
        llm_switcher=services['llm_switcher'],
        config=client_config.schema.llm_configuration
    )

    context_manager = ContextManager()
    context_manager.metadata = {
        'session_id': session_data['session_id'],
        'patient_id': session_data['patient_id'],
        'patient_name': session_data['patient_data'].get('patient_name')
    }

    tool_manager = ToolManager(
        tools=PATIENT_TOOLS,
        config=client_config.schema.tool_configuration
    )

    # 2. Create state transition manager
    state_transition_manager = StateTransitionManager(
        schema=client_config.schema,
        llm_manager=llm_manager,
        context_manager=context_manager,
        tool_manager=tool_manager
    )

    # 3. Create lightweight state manager (just for event handling)
    state_manager = StateManager(
        state_transition_manager=state_transition_manager,
        session_id=session_data['session_id']
    )

    # 4. Set up event handlers
    event_bus = EventBus()
    event_bus.subscribe('state.transition.completed', state_manager.on_transition_completed)
    event_bus.subscribe('state.transition.failed', state_manager.on_transition_failed)

    return {
        'state_manager': state_manager,
        'state_transition_manager': state_transition_manager,
        'llm_manager': llm_manager,
        'context_manager': context_manager,
        'tool_manager': tool_manager,
        'event_bus': event_bus,
        # ... other components
    }
```

### Phase 4: Testing Strategy

#### 4.1 Unit Tests (`tests/test_state_transitions.py`)

```python
import pytest
from unittest.mock import Mock, AsyncMock
from core.state_transition import StateTransitionManager
from core.llm_manager import LLMManager
from core.context_manager import ContextManager

class TestStateTransitions:
    """Comprehensive test suite for state transitions"""

    @pytest.mark.asyncio
    async def test_greeting_clears_only_ivr_context(self):
        """Verify IVR context cleared but metadata preserved"""
        # Setup
        context_manager = ContextManager()
        context_manager.ivr_context = ["IVR message 1", "IVR message 2"]
        context_manager.metadata = {"patient_id": "12345", "session_id": "abc"}

        # Execute
        state_config = {"clear_context": True, "preserve_metadata": True}
        result = await context_manager.prepare_context_for_state("greeting", state_config)

        # Assert
        assert len(context_manager.ivr_context) == 0
        assert context_manager.metadata["patient_id"] == "12345"
        assert any("Patient ID: 12345" in msg.get("content", "")
                  for msg in result if msg["role"] == "system")

    @pytest.mark.asyncio
    async def test_llm_switch_with_fallback(self):
        """Test LLM switching falls back on failure"""
        # Setup
        llm_switcher = Mock()
        llm_switcher.active_llm = Mock()

        config = {
            "state_llm_mapping": {"greeting": "classifier"},
            "default_llm": "main"
        }

        llm_manager = LLMManager(llm_switcher, config)
        llm_manager._perform_switch = AsyncMock(side_effect=Exception("Switch failed"))

        # Execute
        result = await llm_manager.switch_for_state("greeting")

        # Assert
        assert result is False  # Switch failed
        # Verify fallback was attempted

    @pytest.mark.asyncio
    async def test_atomic_transition_rollback(self):
        """Test transition rollback on failure"""
        # Setup
        manager = StateTransitionManager(
            schema=Mock(),
            llm_manager=Mock(),
            context_manager=Mock(),
            tool_manager=Mock()
        )

        # Force failure in step 3
        manager.llm_manager.switch_for_state = AsyncMock(side_effect=Exception("LLM error"))

        # Execute
        result = await manager.transition("verification", "test")

        # Assert
        assert result.success is False
        assert "LLM error" in result.error
        assert manager._current_state == "call_classifier"  # Rolled back

    @pytest.mark.asyncio
    async def test_concurrent_transitions_handled_safely(self):
        """Test that concurrent transitions are serialized"""
        # Setup
        manager = StateTransitionManager(Mock(), Mock(), Mock(), Mock())

        # Execute - attempt concurrent transitions
        results = await asyncio.gather(
            manager.transition("greeting", "reason1"),
            manager.transition("verification", "reason2"),
            return_exceptions=True
        )

        # Assert - only one should succeed
        successful = [r for r in results if r.success]
        assert len(successful) == 1
```

#### 4.2 Integration Tests (`tests/test_integration.py`)

```python
class TestIntegration:
    """End-to-end integration tests"""

    @pytest.mark.asyncio
    async def test_full_call_flow_with_transfer(self):
        """Test complete call flow including transfer detection"""
        # Setup pipeline
        pipeline = await create_test_pipeline()

        # Simulate call flow
        events = [
            CallStartEvent(),
            IVRDetectedEvent(),
            IVRCompletedEvent(),
            # Should be in greeting with classifier LLM
            UserMessageEvent("Hello, this is John"),
            AssistantMessageEvent("Hi John! I'm calling about patient John Doe. "
                                "<next_state>verification</next_state>"),
            # Should switch to main LLM
            UserMessageEvent("Sure, what do you need?"),
            AssistantMessageEvent("I need to verify eligibility..."),
            UserMessageEvent("Let me transfer you to eligibility department"),
            AssistantMessageEvent("I'll hold. <next_state>call_classifier</next_state>"),
            # Should cycle back to classifier
        ]

        for event in events:
            await pipeline.process_event(event)

        # Assert final state
        assert pipeline.state_manager.current_state == "call_classifier"
        assert pipeline.llm_manager.active_llm_type == "classifier"
```

### Phase 5: Monitoring & Observability

#### 5.1 Telemetry Integration

```python
class TelemetryMixin:
    """Mixin for adding telemetry to managers"""

    def _add_span_attributes(self, **attributes):
        """Add attributes to current span"""
        span = trace.get_current_span()
        for key, value in attributes.items():
            span.set_attribute(key, value)

    def _record_metric(self, metric_name: str, value: float, labels: dict = None):
        """Record custom metrics"""
        # Implementation depends on metrics backend
        pass
```

#### 5.2 Production Monitoring Dashboard

Key metrics to track:
- LLM switch success rate
- State transition latency
- Context size by state
- Token usage by LLM type
- Fallback activation frequency
- Concurrent transition attempts

### Implementation Timeline

#### Week 1: Core Infrastructure
- [ ] Implement LLMManager with tests
- [ ] Implement ContextManager with tests
- [ ] Implement StateTransitionManager with tests
- [ ] Create unit test suite

#### Week 2: Configuration & Integration
- [ ] Update YAML schemas
- [ ] Integrate managers into pipeline
- [ ] Update IVR handlers
- [ ] Create integration tests

#### Week 3: Error Handling & Recovery
- [ ] Add retry logic
- [ ] Implement circuit breakers
- [ ] Add fallback mechanisms
- [ ] Create error recovery tests

#### Week 4: Production Readiness
- [ ] Add comprehensive logging
- [ ] Integrate telemetry
- [ ] Performance optimization
- [ ] Load testing
- [ ] Documentation

### Risk Mitigation

1. **Rollout Strategy**: Use feature flags to gradually enable new system
2. **Rollback Plan**: Keep old implementation available for quick revert
3. **Monitoring**: Set up alerts for key metrics before deployment
4. **Testing**: Achieve >80% code coverage before production
5. **Documentation**: Complete API documentation and runbooks

### Success Criteria

- [ ] Zero race conditions in concurrent call scenarios
- [ ] <100ms latency for state transitions
- [ ] 99.9% success rate for LLM switching
- [ ] 50% reduction in token costs through optimal LLM usage
- [ ] Clean separation of concerns with <20 lines per method
- [ ] >80% unit test coverage
- [ ] All integration tests passing

### Conclusion

This refined implementation plan addresses all critical weaknesses of the original approach while maintaining its strengths. The modular architecture ensures reliability, maintainability, and cost efficiency. By following SOLID principles and implementing proper error handling, the system will be production-ready and scalable.

The key improvements are:
1. **Separation of Concerns**: Each manager has a single responsibility
2. **Configuration-Driven**: LLM mappings and behaviors in YAML
3. **Fault Tolerance**: Fallbacks and retry mechanisms throughout
4. **Thread Safety**: Proper locking prevents race conditions
5. **Observable**: Comprehensive telemetry and logging
6. **Testable**: Each component can be tested in isolation

This architecture provides a solid foundation for the voice AI system that can handle complex call flows reliably while optimizing costs through intelligent LLM switching.