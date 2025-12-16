from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger
from backend.models import get_async_patient_db


class PrescriptionStatusFlow:
    """Prescription status inquiry flow for inbound patient calls.

    Flow:
    1. Greeting - Answer and identify as clinic
    2. Verification - Verify patient identity (name, DOB)
    3. Medication Identification - Determine which prescription they're asking about
    4. Status Communication - Share refill status, pharmacy info
    5. Closing - End call
    """

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager = None,
                 main_llm=None, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None):
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self._patient_data = patient_data

    def _init_flow_state(self):
        """Initialize flow_manager state with patient data. Called after flow_manager is set."""
        if not self.flow_manager:
            return

        # Patient record (from clinic database)
        self.flow_manager.state["patient_id"] = self._patient_data.get("patient_id")
        self.flow_manager.state["patient_name"] = self._patient_data.get("patient_name", "")
        self.flow_manager.state["date_of_birth"] = self._patient_data.get("date_of_birth", "")
        self.flow_manager.state["medical_record_number"] = self._patient_data.get("medical_record_number", "")
        self.flow_manager.state["phone_number"] = self._patient_data.get("phone_number", "")

        # Prescription information
        self.flow_manager.state["medication_name"] = self._patient_data.get("medication_name", "")
        self.flow_manager.state["dosage"] = self._patient_data.get("dosage", "")
        self.flow_manager.state["prescribing_physician"] = self._patient_data.get("prescribing_physician", "")
        self.flow_manager.state["refill_status"] = self._patient_data.get("refill_status", "")
        self.flow_manager.state["refills_remaining"] = self._patient_data.get("refills_remaining", 0)
        self.flow_manager.state["last_filled_date"] = self._patient_data.get("last_filled_date", "")
        self.flow_manager.state["next_refill_date"] = self._patient_data.get("next_refill_date", "")

        # Pharmacy information
        self.flow_manager.state["pharmacy_name"] = self._patient_data.get("pharmacy_name", "")
        self.flow_manager.state["pharmacy_phone"] = self._patient_data.get("pharmacy_phone", "")
        self.flow_manager.state["pharmacy_address"] = self._patient_data.get("pharmacy_address", "")

    def _get_global_instructions(self) -> str:
        state = self.flow_manager.state

        return f"""You are a virtual assistant for a medical clinic, answering inbound calls from patients about their prescription refills.

# Voice Conversation Style
You are on a phone call with a patient. Your responses will be converted to speech:
- Speak naturally and warmly, like a helpful clinic staff member
- Keep responses concise and clear
- Use natural acknowledgments: "Of course", "I understand", "Let me check that for you"
- NEVER use bullet points, numbered lists, or markdown formatting

# Patient Record (if verified)
- Patient Name: {state.get("patient_name", "Not yet verified")}
- Date of Birth: {state.get("date_of_birth", "Not yet verified")}

# Prescription Information
- Medication: {state.get("medication_name", "Unknown")}
- Dosage: {state.get("dosage", "Unknown")}
- Refill Status: {state.get("refill_status", "Unknown")}
- Refills Remaining: {state.get("refills_remaining", "Unknown")}
- Last Filled: {state.get("last_filled_date", "Unknown")}

# Pharmacy Information
- Pharmacy: {state.get("pharmacy_name", "Unknown")}
- Phone: {state.get("pharmacy_phone", "Unknown")}
- Address: {state.get("pharmacy_address", "Unknown")}

# HIPAA Compliance
- You MUST verify patient identity before discussing any prescription information
- Ask for full name AND date of birth
- If verification fails, do not provide any prescription details

# Guardrails
- Never provide medical advice about medications
- If prescription needs doctor approval, explain they will be contacted
- If you don't have information, say so honestly
- Stay on topic: prescription status inquiries only"""

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting when patient calls."""
        # TODO: Implement greeting node
        raise NotImplementedError("PrescriptionStatusFlow.create_greeting_node not yet implemented")

    def create_verification_node(self) -> NodeConfig:
        """Verify patient identity with name and DOB."""
        # TODO: Implement verification node
        raise NotImplementedError("PrescriptionStatusFlow.create_verification_node not yet implemented")

    def create_status_node(self) -> NodeConfig:
        """Communicate prescription status to patient."""
        # TODO: Implement status node
        raise NotImplementedError("PrescriptionStatusFlow.create_status_node not yet implemented")

    def create_closing_node(self) -> NodeConfig:
        """Thank patient and end call."""
        # TODO: Implement closing node
        raise NotImplementedError("PrescriptionStatusFlow.create_closing_node not yet implemented")
