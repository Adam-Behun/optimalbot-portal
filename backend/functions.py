import logging
from typing import Optional
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from backend.models import get_async_patient_db

logger = logging.getLogger(__name__)
patient_db = get_async_patient_db()


def convert_spoken_to_numeric(text: str) -> str:
    """Convert spoken numbers to digits while preserving letters."""
    if not text:
        return text
    
    word_to_digit = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
        'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
        'oh': '0'
    }
    
    parts = text.lower().split()
    result = []
    
    for part in parts:
        clean_part = part.strip('.,!?;:-')
        result.append(word_to_digit.get(clean_part, part))
    
    return ''.join(result)


async def update_prior_auth_status_handler(params: FunctionCallParams):
    """Handler for updating prior authorization status."""
    
    patient_id = params.arguments.get("patient_id")
    status = params.arguments.get("status")
    reference_number = params.arguments.get("reference_number")
    
    # Convert spoken numbers to digits
    if reference_number:
        original = reference_number
        reference_number = convert_spoken_to_numeric(reference_number)
        if original != reference_number:
            logger.info(f"Converted: '{original}' → '{reference_number}'")
    
    logger.info(f"Updating patient {patient_id}: status={status}, ref={reference_number}")
    
    # Update database
    success = await patient_db.update_prior_auth_status(patient_id, status, reference_number)
    
    result = {
        "success": success,
        "status": status,
        "reference_number": reference_number
    }
    
    if not success:
        result["error"] = "Database update failed"
    
    await params.result_callback(result)
    logger.info(f"✅ Update {'succeeded' if success else 'failed'}")


# Function schema
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

PATIENT_TOOLS = ToolsSchema(standard_tools=[update_prior_auth_function])

__all__ = ['PATIENT_TOOLS', 'update_prior_auth_status_handler']