# flow_nodes.py
from pipecat_flows import NodeConfig, FlowResult
import logging

logger = logging.getLogger(__name__)

def create_greeting_node(patient_data: dict) -> NodeConfig:
    """Node 1: Initial greeting and introduction"""
    
    # Format patient info for context
    patient_info = f"""
PATIENT INFORMATION (Keep available throughout conversation):
- Name: {patient_data.get('patient_name', 'N/A')}
- Date of Birth: {patient_data.get('date_of_birth', 'N/A')}
- Member ID: {patient_data.get('insurance_member_id', 'N/A')}
- CPT Code: {patient_data.get('cpt_code', 'N/A')}
- Provider NPI: {patient_data.get('provider_npi', 'N/A')}
- Patient ID: {patient_data.get('_id', 'N/A')}
"""
    
    return NodeConfig(
        name="greeting",
        role_messages=[
            {
                "role": "system",
                "content": f"""You are Alexandra from Adam's Medical Practice. You are making an outbound call to verify insurance benefits.
{patient_info}
Keep this patient information available throughout the conversation."""
            }
        ],
        task_messages=[
            {
                "role": "system",
                "content": """You just called an insurance company.

IMPORTANT: You are the CALLER, not the receiver. Wait for the insurance rep to greet you first.

When they answer (e.g., "Hi, this is [name] from [company], how can I help you?"), respond with:
"Hi [their name], this is Alexandra from Adam's Medical Practice. I'm calling to verify eligibility and benefits for a patient."

Keep it brief and natural. Do NOT provide patient details until asked."""
            }
        ],
        functions=[],  # No functions needed in greeting
        respond_immediately=False,  # Wait for insurance rep to speak first
    )

def create_patient_verification_node(patient_data: dict, returning_from_hold: bool = False) -> NodeConfig:
    """Node 2: Provide patient information when asked"""
    
    # Format DOB properly
    dob = patient_data.get('date_of_birth', 'N/A')
    if dob and dob != 'N/A':
        # Convert "1980-01-01" to "January 1st, 1980" for natural speech
        from datetime import datetime
        try:
            date_obj = datetime.strptime(dob, "%Y-%m-%d")
            dob = date_obj.strftime("%B %d, %Y").replace(" 0", " ")
        except:
            pass
    
    hold_context = ""
    if returning_from_hold:
        hold_context = "\n\nNOTE: You were just on hold. The representative has returned. Resume the conversation naturally from where you left off."
    
    return NodeConfig(
        name="patient_verification",
        task_messages=[
            {
                "role": "system",
                "content": f"""The insurance rep will now ask for patient information.

PATIENT INFORMATION TO PROVIDE:
- Name: {patient_data.get('patient_name', 'N/A')}
- Date of Birth: {dob}
- Member ID: {patient_data.get('insurance_member_id', 'N/A')}
- CPT Code: {patient_data.get('cpt_code', 'N/A')}
- Provider NPI: {patient_data.get('provider_npi', 'N/A')}

IMPORTANT RULES:
1. Answer their questions directly and naturally
2. If they ask for the patient's name, say: "The patient's name is {patient_data.get('patient_name', 'N/A')}"
3. If they ask for DOB, say: "Date of birth is {dob}"
4. If they ask for member ID, provide it
5. If they ask what procedure/CPT code, say: "We're looking to verify CPT code {patient_data.get('cpt_code', 'N/A')}"
6. Don't volunteer information they haven't asked for yet

Keep responses short and clear. This is a phone conversation.{hold_context}"""
            }
        ],
        respond_immediately=False,
    )

def create_authorization_check_node(patient_data: dict, returning_from_hold: bool = False) -> NodeConfig:
    """Node 3: Handle authorization status and reference number"""
    
    from pipecat_flows import FlowsFunctionSchema
    from transition_handlers import handle_authorization_update
    
    update_auth_function = FlowsFunctionSchema(
        name="update_prior_auth_status",
        description="Update the prior authorization status and reference number in the database",
        handler=handle_authorization_update,
        required=["status", "reference_number"],
        properties={
            "status": {
                "type": "string",
                "enum": ["Approved", "Denied", "Pending", "Under Review"],
                "description": "The authorization status from the insurance company"
            },
            "reference_number": {
                "type": "string",
                "description": "The reference or authorization number provided by the insurance company"
            }
        }
    )
    
    hold_context = ""
    if returning_from_hold:
        hold_context = "\n\nNOTE: You were just on hold. The representative has returned. Resume the conversation naturally from where you left off."
    
    return NodeConfig(
        name="authorization_check",
        task_messages=[
            {
                "role": "system",
                "content": f"""You are checking authorization for CPT code {patient_data.get('cpt_code', 'N/A')} for patient {patient_data.get('patient_name', 'N/A')}.

CRITICAL WORKFLOW:
1. LISTEN for the authorization status (approved/denied/pending)
2. AS SOON as you hear the status, IMMEDIATELY:
   - Acknowledge the status
   - Ask: "Can I have the reference number for our records please?"
3. When they provide the reference number:
   - Confirm you received it
   - IMMEDIATELY call update_prior_auth_status with BOTH the status AND reference_number

EXAMPLE:
Insurance: "The authorization is approved"
You: "Great, it's approved! Can I have the reference number for our records please?"
Insurance: "Yes, it's 12345"
You: "Thank you, I have reference number 12345"
[IMMEDIATELY call update_prior_auth_status(status="Approved", reference_number="12345")]

DO NOT end the conversation or thank them until AFTER you've called the function.
The function updates the database with patient ID: {patient_data.get('_id', 'N/A')}{hold_context}"""
            }
        ],
        functions=[update_auth_function],
        respond_immediately=False,
    )

def create_closing_node() -> NodeConfig:
    """Node 4: Thank and close the call"""
    
    return NodeConfig(
        name="closing",
        task_messages=[
            {
                "role": "system",
                "content": """Thank the insurance representative and end the call professionally.

Say something like: "Thank you so much for your help, [their name]. Have a great day!"

Keep it brief and friendly."""
            }
        ],
        respond_immediately=True,
    )

def create_hold_return_node(patient_data: dict) -> NodeConfig:
    """Node for confirming return from hold"""
    return NodeConfig(
        name="hold_return",
        task_messages=[
            {
                "role": "system",
                "content": """You were on hold and think the representative may have returned.

Say ONLY: "Yes, I'm here."

Then WAIT for the representative to respond. Do not say anything else yet."""
            }
        ],
        respond_immediately=True,
        functions=[],
    )