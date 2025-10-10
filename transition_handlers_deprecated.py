from pipecat_flows import FlowArgs, FlowManager, FlowResult, NodeConfig
from flow_nodes import (
    create_patient_verification_node,
    create_authorization_check_node,
    create_closing_node
)
import logging

logger = logging.getLogger(__name__)

async def transition_to_verification(
    args: FlowArgs, flow_manager: FlowManager
) -> tuple[FlowResult, NodeConfig]:
    """Transition from greeting to patient verification"""
    
    # Get patient data from state
    patient_data = flow_manager.state.get("patient_data", {})
    
    # Store any insurance rep name if mentioned
    if "rep_name" in args:
        flow_manager.state["collected_info"]["insurance_rep_name"] = args["rep_name"]
    
    # Create and return next node
    next_node = create_patient_verification_node(patient_data)
    
    return {"status": "ready_for_verification"}, next_node

async def transition_to_authorization(
    args: FlowArgs, flow_manager: FlowManager
) -> tuple[FlowResult, NodeConfig]:
    """Transition from verification to authorization check"""
    
    # Get patient data from state
    patient_data = flow_manager.state.get("patient_data", {})
    
    # Create and return next node
    next_node = create_authorization_check_node(patient_data)
    
    return {"status": "verification_complete"}, next_node

async def handle_authorization_update(
    args: FlowArgs, flow_manager: FlowManager
) -> tuple[FlowResult, NodeConfig]:
    """Handle authorization status update and transition to closing"""
    
    # Import the actual function
    from functions import update_prior_auth_status
    
    # Get patient ID from state
    patient_data = flow_manager.state.get("patient_data", {})
    patient_id = patient_data.get('_id')
    status = args.get("status")
    reference_number = args.get("reference_number")
    
    logger.info(f"Updating auth for patient {patient_id}: status={status}, ref={reference_number}")
    
    # Update database
    if patient_id and status:
        success = await update_prior_auth_status(patient_id, status, reference_number)
        
        # Store in state
        flow_manager.state["collected_info"]["auth_status"] = status
        flow_manager.state["collected_info"]["reference_number"] = reference_number
        
        if success:
            logger.info(f"Successfully updated auth status to {status} with ref {reference_number}")
        else:
            logger.error("Failed to update auth status in database")
    else:
        logger.error(f"Missing required data: patient_id={patient_id}, status={status}")
    
    # Transition to closing
    next_node = create_closing_node()
    
    return {"status": "authorization_complete", "auth_status": status, "reference": reference_number}, next_node