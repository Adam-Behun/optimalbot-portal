"""LLM function call execution"""

from loguru import logger
from functions import update_prior_auth_status_handler
from monitoring import emit_event


def setup_function_call_handler(pipeline):
    """Setup handler for LLM function calls"""
    
    @pipeline.llm.event_handler("on_function_call")
    async def handle_function_call(llm, function_name, arguments):
        logger.info(f"🔧 Function call: {function_name}")
        
        func = FUNCTION_REGISTRY.get(function_name)
        if not func:
            logger.error(f"Function not found: {function_name}")
            return {"error": f"Function {function_name} not found"}
        
        # Add patient_id if missing
        if "patient_id" not in arguments:
            arguments["patient_id"] = pipeline.patient_id
        
        try:
            result = await func(**arguments)
            
            emit_event(
                session_id=pipeline.session_id,
                category="FUNCTION",
                event="function_executed",
                metadata={
                    "function_name": function_name,
                    "result": result
                }
            )
            
            return {"success": result}
            
        except Exception as e:
            logger.error(f"Function error ({function_name}): {e}")
            return {"error": str(e)}