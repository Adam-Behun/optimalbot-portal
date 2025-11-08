import logging
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from backend.models import get_async_patient_db

logger = logging.getLogger(__name__)


def convert_spoken_to_numeric(text: str) -> str:
    """Convert spoken numbers to digits (e.g., 'one two three' -> '123')"""
    if not text:
        return text
    
    word_to_digit = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
        'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
        'oh': '0'
    }
    
    parts = text.lower().split()
    converted = [word_to_digit.get(part.strip('.,!?;:-'), part) for part in parts]
    
    return ''.join(converted)


async def update_prior_auth_status_handler(params: FunctionCallParams):
    """Handler for updating prior authorization status via LLM function call"""

    logger.info("🔧 Function call: update_prior_auth_status")

    patient_id = params.arguments.get("patient_id")
    status = params.arguments.get("status")
    reference_number = params.arguments.get("reference_number")

    # Convert spoken numbers to digits
    if reference_number:
        original = reference_number
        reference_number = convert_spoken_to_numeric(reference_number)
        if original != reference_number:
            logger.info(f"Converted reference number: '{original}' → '{reference_number}'")

    logger.info(f"Updating patient {patient_id}: status={status}, ref={reference_number}")

    # Update database
    patient_db = get_async_patient_db()
    success = await patient_db.update_prior_auth(patient_id, status, reference_number)

    result = {
        "success": success,
        "status": status,
        "reference_number": reference_number
    }

    if not success:
        result["error"] = "Database update failed"
        logger.error(f"Failed to update patient {patient_id}")
    else:
        logger.info(f"✅ Successfully updated patient {patient_id}")

    await params.result_callback(result)


async def dial_supervisor_handler(params: FunctionCallParams, transport, patient_data, pipeline):
    supervisor_phone = patient_data.get("supervisor_phone")

    if not supervisor_phone:
        logger.warning("Transfer requested but no supervisor phone configured")
        result = {
            "success": False,
            "error": "No supervisor phone available"
        }
        await params.result_callback(result)
        return

    import re
    if not re.match(r'^\+\d{10,15}$', supervisor_phone):
        logger.error(f"Invalid supervisor phone format: {supervisor_phone}")
        result = {
            "success": False,
            "error": "Invalid supervisor phone format"
        }
        await params.result_callback(result)
        return

    logger.info(f"Initiating transfer to supervisor: {supervisor_phone}")

    try:
        pipeline.transfer_in_progress = True

        patient_id = patient_data.get("patient_id")
        if patient_id:
            patient_db = get_async_patient_db()
            await patient_db.update_call_status(patient_id, "Supervisor Requested")
            logger.info(f"Updated call_status to 'Supervisor Requested' for patient {patient_id}")

        if hasattr(pipeline, 'transcripts'):
            from datetime import datetime
            system_message = {
                "role": "system",
                "content": "Supervisor Requested",
                "timestamp": datetime.now().isoformat(),
                "type": "system_event"
            }
            pipeline.transcripts.append(system_message)
            logger.info("Added 'Supervisor Requested' to transcript")

        transfer_params = {"toEndPoint": supervisor_phone}
        await transport.sip_call_transfer(transfer_params)

        result = {
            "success": True,
            "transferred_to": supervisor_phone
        }
        logger.info(f"Transfer initiated to {supervisor_phone}")

    except Exception as e:
        logger.error(f"Transfer failed: {e}")
        pipeline.transfer_in_progress = False
        result = {
            "success": False,
            "error": f"Transfer failed: {str(e)}"
        }

    await params.result_callback(result)


# Function schema for LLM
update_prior_auth_function = FunctionSchema(
    name="update_prior_auth_status",
    description="Update prior authorization status and reference number from insurance company",
    properties={
        "patient_id": {
            "type": "string",
            "description": "Patient's MongoDB ObjectId"
        },
        "status": {
            "type": "string",
            "description": "Authorization status",
            "enum": ["Approved", "Denied", "Pending", "Under Review"]
        },
        "reference_number": {
            "type": "string",
            "description": "Reference or authorization number from insurance"
        }
    },
    required=["patient_id", "status"]
)

dial_supervisor_function = FunctionSchema(
    name="dial_supervisor",
    description="Transfer the call to a human supervisor when requested by the insurance representative or when escalation is needed",
    properties={},
    required=[]
)

PATIENT_TOOLS = ToolsSchema(standard_tools=[update_prior_auth_function, dial_supervisor_function])

__all__ = ['PATIENT_TOOLS', 'update_prior_auth_status_handler', 'dial_supervisor_handler']