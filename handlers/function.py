"""LLM function call execution"""

from loguru import logger
from backend.functions import update_prior_auth_status_handler, dial_supervisor_handler


def setup_function_call_handler(pipeline):
    """Setup handler for LLM function calls"""

    @pipeline.llm.event_handler("on_function_call")
    async def handle_function_call(llm, function_name, params):
        logger.info(f"🔧 Function call: {function_name}")

        # Add patient_id to arguments if missing
        if "patient_id" not in params.arguments:
            params.arguments["patient_id"] = pipeline.patient_id

        # Route to appropriate handler
        if function_name == "update_prior_auth_status":
            await update_prior_auth_status_handler(params)
        elif function_name == "dial_supervisor":
            await dial_supervisor_handler(params, pipeline.transport, pipeline.patient_data, pipeline)
        else:
            logger.error(f"Unknown function: {function_name}")
            await params.result_callback({"error": f"Function {function_name} not found"})