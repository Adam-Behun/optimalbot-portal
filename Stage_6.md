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

STAGE 6: Pipeline Integration with Latency Tracking
Context for LLM Expert
You are integrating all schema components into the call pipeline with comprehensive latency tracking at each step. This proves the system works end-to-end and identifies any bottlenecks.
Files You Need to Request
Please provide:
1. Complete pipeline.py
2. Current app.py with /start-call endpoint
3. All engine/ modules from previous stages
4. How calls are currently initialized and managed
Implementation Requirements
6.1 Update Pipeline with Schema Components
python# In pipeline.py

class HealthcareAIPipeline:
    """
    Voice pipeline using schema-driven conversation management.
    """
    
    def __init__(
        self,
        patient_data: dict,
        session_id: str,
        conversation_state: ConversationState,  # NEW: From factory
        precomputed_data: dict,                 # NEW: Pre-formatted data
        prompt_renderer: PromptRenderer,        # NEW: Shared renderer
        functions_registry: dict
    ):
        # Store schema components
        self.session_id = session_id
        self.conversation_state = conversation_state
        self.precomputed_data = precomputed_data
        self.prompt_renderer = prompt_renderer
        self.functions_registry = functions_registry
        
        # Performance tracking
        self.latency_tracker = {
            "precompute_ms": 0.0,
            "prompt_renders": [],
            "state_transitions": [],
            "total_call_time_ms": 0.0
        }
        
        self._call_start_time = time.perf_counter()
        
        # Existing pipeline initialization...
        # (your STT, LLM, TTS setup)
    
    async def initialize_first_node(self):
        """Create first node using schema."""
        start_time = time.perf_counter()
        
        # Get initial state from conversation state
        initial_state = self.conversation_state.current_state
        
        # Render prompt (fast - using precomputed data)
        prompt = self.prompt_renderer.render_state_prompt(
            initial_state,
            self.precomputed_data,
            streaming=True
        )
        
        # Create node config for Pipecat
        initial_node = NodeConfig(
            name=initial_state,
            task_messages=[{
                "role": "system",
                "content": prompt
            }],
            respond_immediately=False
        )
        
        # Track performance
        init_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"Initialized first node '{initial_state}' in {init_ms:.2f}ms")
        
        await self.flow_manager.initialize(initial_node)
    
    async def handle_user_message(self, message: str):
        """Process user message and potentially transition state."""
        start_time = time.perf_counter()
        
        # Classify intent and get next state
        next_state = await self.conversation_state.process_user_message(message)
        
        # If state changed, create new node
        if next_state != self.conversation_state.current_state:
            await self._transition_to_state(next_state)
        
        # Track transition time
        transition_ms = (time.perf_counter() - start_time) * 1000
        self.latency_tracker["state_transitions"].append({
            "from": self.conversation_state.current_state,
            "to": next_state,
            "time_ms": transition_ms
        })
    
    async def _transition_to_state(self, state_name: str):
        """Transition to new state with latency tracking."""
        start_time = time.perf_counter()
        
        # Render new prompt
        prompt = self.prompt_renderer.render_state_prompt(
            state_name,
            self.precomputed_data,
            streaming=True
        )
        
        # Get state config
        state_def = self.conversation_state.schema.get_state(state_name)
        
        # Create new node
        node = NodeConfig(
            name=state_name,
            task_messages=[{
                "role": "system",
                "content": prompt
            }],
            functions=self._get_functions_for_state(state_def),
            respond_immediately=state_def.respond_immediately
        )
        
        # Transition in flow manager
        await self.flow_manager.set_node(node)
        
        # Track performance
        render_ms = (time.perf_counter() - start_time) * 1000
        self.latency_tracker["prompt_renders"].append({
            "state": state_name,
            "time_ms": render_ms
        })
        
        logger.info(f"Transitioned to '{state_name}' in {render_ms:.2f}ms")
    
    def _get_functions_for_state(self, state_def):
        """Get function schemas for state."""
        functions = []
        for func_config in state_def.functions:
            func_name = func_config['name']
            if func_name in self.functions_registry:
                functions.append(self.functions_registry[func_name])
        return functions
    
    def get_performance_report(self) -> dict:
        """Get comprehensive performance metrics."""
        total_call_ms = (time.perf_counter() - self._call_start_time) * 1000
        
        avg_render_ms = (
            sum(r["time_ms"] for r in self.latency_tracker["prompt_renders"]) /
            len(self.latency_tracker["prompt_renders"])
            if self.latency_tracker["prompt_renders"] else 0
        )
        
        return {
            "total_call_time_ms": total_call_ms,
            "precompute_time_ms": self.latency_tracker["precompute_ms"],
            "avg_prompt_render_ms": avg_render_ms,
            "state_transitions": len(self.latency_tracker["state_transitions"]),
            "transition_details": self.latency_tracker["state_transitions"],
            "schema_overhead_estimate_ms": (
                self.latency_tracker["precompute_ms"] + 
                avg_render_ms * len(self.latency_tracker["prompt_renders"])
            )
        }
6.2 Update /start-call with Pre-computation
python# In app.py

@app.post("/start-call")
async def start_call(patient_id: str):
    """Start a call with schema-driven pipeline."""
    
    # Fetch patient data (existing code)
    patient_dict = await get_patient_data(patient_id)
    
    # Validate against schema
    try:
        conversation_schema.validate_data(patient_dict)
    except ValueError as e:
        logger.error(f"Invalid patient data: {e}")
        return {"error": f"Invalid patient data: {e}"}, 400
    
    # PRE-COMPUTE all data formatting (ONCE per call)
    precompute_start = time.perf_counter()
    precomputed_data = data_precomputer.precompute_all(patient_dict)
    precompute_ms = (time.perf_counter() - precompute_start) * 1000
    
    logger.info(f"Pre-computed patient data in {precompute_ms:.2f}ms")
    
    # Create lightweight conversation state
    session_id = generate_session_id()
    conversation_state = state_manager_factory.create_state(session_id)
    
    # Create pipeline with all components
    pipeline = HealthcareAIPipeline(
        patient_data=patient_dict,
        session_id=session_id,
        conversation_state=conversation_state,
        precomputed_data=precomputed_data,  # Pre-formatted data
        prompt_renderer=prompt_renderer,    # Shared renderer
        functions_registry=functions_registry
    )
    
    # Track precompute time in pipeline
    pipeline.latency_tracker["precompute_ms"] = precompute_ms
    
    # Rest of call setup...
    
    return {"session_id": session_id, "status": "started"}


@app.get("/call-performance/{session_id}")
async def get_call_performance(session_id: str):
    """Get performance metrics for a call"""
    # Get pipeline for session
    pipeline = get_pipeline_for_session(session_id)
    
    if not pipeline:
        return {"error": "Session not found"}, 404
    
    return pipeline.get_performance_report()
Success Criteria

 Calls start successfully with schema system
 Pre-compute time <50ms logged
 Each state transition <20ms total
 Performance report shows schema overhead
 No regression in call quality

What to Ask the Engineer
Before implementing, please provide:
1. Complete pipeline.py
2. Complete app.py
3. All engine/ files from previous stages
4. How you manage session state currently
5. Your Pipecat Flows setup

I need to integrate schema into your existing pipeline without breaking it.