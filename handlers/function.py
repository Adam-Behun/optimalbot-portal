from loguru import logger
from backend.functions import dial_supervisor_handler


def setup_function_call_handler(pipeline):
    async def dial_supervisor_wrapper(params):
        """Wrapper that provides access to pipeline context"""
        logger.info(f"🔧 Function call: dial_supervisor")
        await dial_supervisor_handler(params, pipeline.transport, pipeline.patient_data, pipeline)
    pipeline.main_llm.register_function("dial_supervisor", dial_supervisor_wrapper)

    logger.debug("✅ dial_supervisor function handler registered on main_llm")