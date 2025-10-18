import re
import time
import logging
from typing import Optional, Dict, Any
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from models import get_async_patient_db, AsyncPatientRecord

logger = logging.getLogger(__name__)

# Initialize database connection
patient_db = get_async_patient_db()

async def update_prior_auth_status_handler(params: FunctionCallParams):
    start_time = time.time()
    
    try:
        patient_id = params.arguments.get("patient_id")
        status = params.arguments.get("status")
        reference_number = params.arguments.get("reference_number")

        if reference_number:
            original_ref = reference_number
            reference_number = convert_spoken_to_alphanumeric(reference_number)
            logger.info(f"Converted reference: '{original_ref}' -> '{reference_number}'")
        
        logger.info(f"Attempting to update patient {patient_id} to status '{status}'")
        if reference_number:
            logger.info(f"With reference number: {reference_number}")
        
        patient = await patient_db.find_patient_by_id(patient_id)
        if not patient:
            logger.error(f"Patient not found: {patient_id}")
            await params.result_callback({"success": False, "error": "Patient not found"})
            return
            
        success = await patient_db.update_prior_auth_status(patient_id, status, reference_number)
        
        latency = (time.time() - start_time) * 1000
        logger.info(f"Prior auth update latency: {latency:.2f}ms")
        
        if success:
            logger.info(f"Successfully updated prior auth status to '{status}' for patient ID: {patient_id}")
            
            # Verify the update
            updated_patient = await patient_db.find_patient_by_id(patient_id)
            logger.info(f"Verification - new status: {updated_patient.get('prior_auth_status', 'ERROR')}")
            if reference_number:
                logger.info(f"Verification - reference: {updated_patient.get('reference_number', 'NOT SET')}")
            
            await params.result_callback({
                "success": True,
                "status": status,
                "reference_number": reference_number
            })
        else:
            logger.error(f"Failed to update prior auth status for patient ID: {patient_id}")
            await params.result_callback({"success": False, "error": "Database update failed"})
            
    except Exception as e:
        logger.error(f"Exception updating prior auth status: {e}")
        await params.result_callback({"success": False, "error": str(e)})

def convert_spoken_to_alphanumeric(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    
    number_map = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
        'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
        'oh': '0'
    }
    
    phonetic_map = {
        'alpha': 'A', 'bravo': 'B', 'charlie': 'C', 'delta': 'D', 'echo': 'E',
        'foxtrot': 'F', 'golf': 'G', 'hotel': 'H', 'india': 'I', 'juliet': 'J',
        'kilo': 'K', 'lima': 'L', 'mike': 'M', 'november': 'N', 'oscar': 'O',
        'papa': 'P', 'quebec': 'Q', 'romeo': 'R', 'sierra': 'S', 'tango': 'T',
        'uniform': 'U', 'victor': 'V', 'whiskey': 'W', 'xray': 'X', 'yankee': 'Y',
        'zulu': 'Z'
    }
    
    words = text.lower().split()
    result = []
    
    for word in words:
        word = re.sub(r'[^\w]', '', word)
        
        if word in number_map:
            result.append(number_map[word])
        elif word in phonetic_map:
            result.append(phonetic_map[word])
        elif len(word) == 1 and word.isalpha():
            result.append(word.upper())
        elif word.isdigit():
            result.append(word)    
    return ''.join(result) if result else text

# Function definitions using Pipecat's standard schema (provider-agnostic)
update_prior_auth_function = FunctionSchema(
    name="update_prior_auth_status",
    description="Update the prior authorization status and reference number for a patient when received from insurance company",
    properties={
        "patient_id": {
            "type": "string",
            "description": "Patient's MongoDB ObjectId"
        },
        "status": {
            "type": "string",
            "description": "New authorization status",
            "enum": ["Approved", "Denied", "Pending", "Under Review"]
        },
        "reference_number": {
            "type": "string",
            "description": "Reference or authorization number from insurance company"
        }
    },
    required=["patient_id", "status"]
)

# ✅ Create tools schema
PATIENT_TOOLS = ToolsSchema(
    standard_tools=[update_prior_auth_function]
)

# Export for use in pipeline
__all__ = ['PATIENT_TOOLS', 'update_prior_auth_status_handler']