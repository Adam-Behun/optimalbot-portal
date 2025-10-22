"""LLM function call execution"""

from loguru import logger
from backend.functions import update_prior_auth_status_handler
from monitoring import add_span_attributes


# Function registry
FUNCTION_REGISTRY = {
    "update_prior_auth_status": update_prior_auth_status_handler,
}


def setup_function_call_handler(pipeline):
    """Setup handler for LLM function calls"""
    
    @pipeline.llm.event_handler("on_function_call")
    async def handle_function_call(llm, function_name, arguments):
        logger.info(f"🔧 Function call: {function_name}")
        
        func = FUNCTION_REGISTRY.get(function_name)
        if not func:
            logger.error(f"Function not found: {function_name}")
            add_span_attributes(
                **{
                    "function.name": function_name,
                    "function.status": "not_found",
                    "error.type": "function_not_found",
                }
            )
            return {"error": f"Function {function_name} not found"}
        
        # Add patient_id if missing
        if "patient_id" not in arguments:
            arguments["patient_id"] = pipeline.patient_id
        
        try:
            result = await func(**arguments)
            
            add_span_attributes(
                **{
                    "function.name": function_name,
                    "function.status": "success",
                    "function.result": str(result),
                }
            )
            
            return {"success": result}
            
        except Exception as e:
            logger.error(f"Function error ({function_name}): {e}")
            add_span_attributes(
                **{
                    "function.name": function_name,
                    "function.status": "error",
                    "error.type": "function_execution_error",
                    "error.message": str(e),
                }
            )
            return {"error": str(e)}