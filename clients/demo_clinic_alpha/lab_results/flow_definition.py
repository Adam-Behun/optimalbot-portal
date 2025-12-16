from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger
from backend.models import get_async_patient_db


class LabResultsFlow:
    """Lab results inquiry flow for inbound patient calls.

    Flow:
    1. Greeting - Answer and identify as clinic
    2. Verification - Verify patient identity (name, DOB)
    3. Lookup - Find patient's lab results
    4. Communication - Share results or explain next steps
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

        # Lab order information
        self.flow_manager.state["test_type"] = self._patient_data.get("test_type", "")
        self.flow_manager.state["test_date"] = self._patient_data.get("test_date", "")
        self.flow_manager.state["ordering_physician"] = self._patient_data.get("ordering_physician", "")
        self.flow_manager.state["results_status"] = self._patient_data.get("results_status", "")
        self.flow_manager.state["results_summary"] = self._patient_data.get("results_summary", "")
        self.flow_manager.state["provider_review_required"] = self._patient_data.get("provider_review_required", False)
        self.flow_manager.state["callback_timeframe"] = self._patient_data.get("callback_timeframe", "")

    def _get_global_instructions(self) -> str:
        state = self.flow_manager.state

        return f"""You are a virtual assistant for a medical clinic, answering inbound calls from patients about their lab results.

# Voice Conversation Style
You are on a phone call with a patient. Your responses will be converted to speech:
- Speak naturally and warmly, like a helpful clinic staff member
- Keep responses concise and clear
- Use natural acknowledgments: "Of course", "I understand", "Let me check that for you"
- NEVER use bullet points, numbered lists, or markdown formatting

# Patient Record (if verified)
- Patient Name: {state.get("patient_name", "Not yet verified")}
- Date of Birth: {state.get("date_of_birth", "Not yet verified")}
- Test Type: {state.get("test_type", "Unknown")}
- Test Date: {state.get("test_date", "Unknown")}
- Results Status: {state.get("results_status", "Unknown")}

# HIPAA Compliance
- You MUST verify patient identity before discussing any health information
- Ask for full name AND date of birth
- If verification fails, do not provide any lab information

# Guardrails
- Never interpret or diagnose based on lab results
- For abnormal results, advise the patient that the doctor will contact them
- If you don't have information, say so honestly
- Stay on topic: lab results inquiries only"""

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting when patient calls."""
        # TODO: Implement greeting node
        raise NotImplementedError("LabResultsFlow.create_greeting_node not yet implemented")

    def create_verification_node(self) -> NodeConfig:
        """Verify patient identity with name and DOB."""
        # TODO: Implement verification node
        raise NotImplementedError("LabResultsFlow.create_verification_node not yet implemented")

    def create_results_node(self) -> NodeConfig:
        """Communicate lab results or status to patient."""
        # TODO: Implement results node
        raise NotImplementedError("LabResultsFlow.create_results_node not yet implemented")

    def create_closing_node(self) -> NodeConfig:
        """Thank patient and end call."""
        # TODO: Implement closing node
        raise NotImplementedError("LabResultsFlow.create_closing_node not yet implemented")
