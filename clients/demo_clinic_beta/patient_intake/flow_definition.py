import logging
from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema, ContextStrategy, ContextStrategyConfig
from pipecat.frames.frames import EndTaskFrame, ManuallySwitchServiceFrame
from pipecat.processors.frame_processor import FrameDirection
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db

logger = logging.getLogger(__name__)


class PatientIntakeFlow:
    """Dial-in call flow for patient intake and appointment scheduling - Demo Clinic Beta.

    Handles both new and returning patients, collects appointment preferences,
    and schedules appointments with a warm, friendly, and supportive tone.
    """

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager,
                 main_llm, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None):
        self.patient_data = patient_data
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm  # Secondary LLM for specific nodes
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id

        # Patient type and service
        self.is_new_patient = None
        self.service_type = None  # "whitening", "new_patient", "checkup", "urgent"

        # Appointment details
        self.appointment_date = None
        self.appointment_time = None

        # Collected patient info
        self.collected_first_name = None
        self.collected_last_name = None
        self.collected_phone = None
        self.collected_dob = None
        self.collected_email = None

    def _get_global_instructions(self) -> str:
        """Global behavioral rules applied to all states."""
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        return f"""BEHAVIORAL RULES:
1. You are a Virtual Assistant from {facility_name}. Always disclose this.
2. Speak ONLY in English.
3. Be WARM, EXCITED, FRIENDLY, and SUPPORTIVE throughout the entire conversation.
4. Keep responses conversational and natural - like talking to a friend!
5. Use enthusiastic language and make the patient feel valued and welcome.
6. This is an inbound call - the patient is calling us.
7. Once you learn the patient's name, use it to personalize the conversation."""

    def create_greeting_node(self) -> NodeConfig:
        """Initial greeting node when caller connects.

        Uses classifier_llm (fast) for conversational greeting.
        No function calling - just greets and asks if new/returning patient.
        Transitions via proceed_to_patient_type_selection function.
        """
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        return NodeConfig(
            name="greeting",
            role_messages=[{
                "role": "system",
                "content": f"""You are a warm, friendly Virtual Assistant from {facility_name}.
Speak ONLY in English. Be enthusiastic and make callers feel welcome!"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""A caller has connected. Greet them warmly!

Say: "Hello! Thank you for calling {facility_name}! I'm your Virtual Assistant and I'm happy to help you today! Are you a new patient with us, or have you visited us before?"

After greeting, immediately call proceed_to_patient_type_selection to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_patient_type_selection",
                    description="Transition to patient type selection after greeting.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_patient_type_selection_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_patient_type_selection_node(self) -> NodeConfig:
        """Node to determine if caller is new or returning patient.

        Uses main_llm for accurate classification with function calling.
        Resets context since greeting is complete.
        """
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        return NodeConfig(
            name="patient_type_selection",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}. Be warm and professional."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the caller's response about whether they are a new or returning patient.

Based on their response:
- If they're a NEW patient (first time, never been here): call set_new_patient
- If they're a RETURNING patient (been here before, existing patient): call set_returning_patient

If unclear, ask: "Just to confirm - is this your first visit with us?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="set_new_patient",
                    description="Patient is new to the clinic - proceed to service selection.",
                    properties={},
                    required=[],
                    handler=self._set_new_patient_handler
                ),
                FlowsFunctionSchema(
                    name="set_returning_patient",
                    description="Patient has visited before - proceed to reason for visit.",
                    properties={},
                    required=[],
                    handler=self._set_returning_patient_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET
            )
        )

    def create_new_patient_service_node(self) -> NodeConfig:
        """Node to ask new patients what service they're interested in.

        Uses classifier_llm (fast) for conversational question.
        Transitions to service_selection node for classification.
        """
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        return NodeConfig(
            name="new_patient_service",
            role_messages=[{
                "role": "system",
                "content": f"""You are a warm Virtual Assistant from {facility_name}. This is a NEW patient - be extra welcoming!"""
            }],
            task_messages=[{
                "role": "system",
                "content": """Express how happy you are to have them and ask what brings them in.

Say: "That's wonderful! We're so happy to have you! What are you looking to do today? Are you interested in our Professional Teeth Whitening service, or would you like to schedule a New Patient Appointment?"

After asking, call proceed_to_service_selection to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_service_selection",
                    description="Transition to service selection after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_service_selection_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_new_patient_service_selection_node(self) -> NodeConfig:
        """Node to classify which service the new patient wants.

        Uses main_llm for accurate classification with function calling.
        """
        return NodeConfig(
            name="new_patient_service_selection",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Listen carefully to classify the patient's service choice."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Based on the patient's response, determine which service they want:

- If they want TEETH WHITENING: call select_teeth_whitening
- If they want a NEW PATIENT APPOINTMENT (checkup, cleaning, general visit): call select_new_patient_appointment

If unclear, ask: "Just to confirm - would you like our Professional Teeth Whitening, or a general New Patient Appointment?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="select_teeth_whitening",
                    description="Patient wants Professional Teeth Whitening service.",
                    properties={},
                    required=[],
                    handler=self._select_teeth_whitening_handler
                ),
                FlowsFunctionSchema(
                    name="select_new_patient_appointment",
                    description="Patient wants a general New Patient Appointment.",
                    properties={},
                    required=[],
                    handler=self._select_new_patient_appointment_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_returning_patient_reason_node(self) -> NodeConfig:
        """Node to ask returning patients their reason for visiting.

        Uses classifier_llm (fast) for conversational welcome back.
        Transitions to reason_selection node for classification.
        """
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        return NodeConfig(
            name="returning_patient_reason",
            role_messages=[{
                "role": "system",
                "content": f"""You are a warm Virtual Assistant from {facility_name}. This is a RETURNING patient - welcome them back!"""
            }],
            task_messages=[{
                "role": "system",
                "content": """Welcome the returning patient back warmly!

Say: "Welcome back! It's so great to hear from you again! What can we help you with today? Are you due for a regular check-up, or is there something specific bothering you?"

After asking, call proceed_to_reason_selection to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_reason_selection",
                    description="Transition to reason selection after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_reason_selection_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_returning_patient_reason_selection_node(self) -> NodeConfig:
        """Node to classify returning patient's reason for visit.

        Uses main_llm for accurate classification with function calling.
        """
        return NodeConfig(
            name="returning_patient_reason_selection",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Listen carefully to classify the patient's reason for visit."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Based on the patient's response, determine their reason for visiting:

- If they need a REGULAR CHECK-UP (routine, cleaning, just due): call select_checkup
- If they have PAIN or a specific issue (toothache, discomfort, problem): call select_pain_issue with issue_description

If unclear, ask: "Just to confirm - is this for a routine check-up, or do you have a specific concern?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="select_checkup",
                    description="Returning patient needs a regular check-up.",
                    properties={},
                    required=[],
                    handler=self._select_checkup_handler
                ),
                FlowsFunctionSchema(
                    name="select_pain_issue",
                    description="Returning patient has pain or a specific dental issue.",
                    properties={
                        "issue_description": {
                            "type": "string",
                            "description": "Brief description of the patient's pain or issue"
                        }
                    },
                    required=[],
                    handler=self._select_pain_issue_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_date_selection_node(self) -> NodeConfig:
        """Node to ask which date works best for the patient.

        Uses classifier_llm (fast) for conversational question.
        Transitions to date_processing node for extraction.
        """
        service_display = {
            "whitening": "Professional Teeth Whitening",
            "new_patient": "New Patient Appointment",
            "checkup": "Check-up",
            "urgent": "Dental Visit"
        }.get(self.service_type, "appointment")

        return NodeConfig(
            name="date_selection",
            role_messages=[{
                "role": "system",
                "content": f"""You are a friendly Virtual Assistant scheduling a {service_display}."""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""Ask the patient which day works best for their {service_display}.

Say: "Perfect! Which day works best for you? Just let me know the date you have in mind!"

After asking, call proceed_to_date_processing to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_date_processing",
                    description="Transition to date processing after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_date_processing_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET_WITH_SUMMARY,
                summary_prompt="Summarize: patient type (new/returning), service requested. Keep under 30 words."
            )
        )

    def create_date_processing_node(self) -> NodeConfig:
        """Node to extract and process the date from patient's response.

        Uses main_llm for accurate date extraction with function calling.
        """
        return NodeConfig(
            name="date_processing",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Extract dates accurately from natural language."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the patient's response and extract the date they mentioned.

When they provide a date, call check_date_availability with the date.
Accept various formats: "next Monday", "December 15th", "12/15", "tomorrow", etc.

If unclear, ask: "What date works best for you?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="check_date_availability",
                    description="Check available time slots for the requested date.",
                    properties={
                        "requested_date": {
                            "type": "string",
                            "description": "The date the patient requested (e.g., 'next Monday', 'December 15', '12/15/2024')"
                        }
                    },
                    required=["requested_date"],
                    handler=self._check_date_availability_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_time_selection_node(self) -> NodeConfig:
        """Node to present available time slots and let patient choose.

        Uses classifier_llm (fast) to present options conversationally.
        Transitions to time_processing node for extraction.
        """
        # Mock available times - in production, this would come from a scheduling system
        available_times = ["9:00 AM", "10:30 AM", "1:00 PM", "3:30 PM"]

        return NodeConfig(
            name="time_selection",
            role_messages=[{
                "role": "system",
                "content": f"""You are a friendly Virtual Assistant. The appointment date is {self.appointment_date}."""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""Present the available time slots enthusiastically!

Say: "Great news! On {self.appointment_date}, we have openings at {', '.join(available_times[:-1])}, and {available_times[-1]}. Which time works best for you?"

After presenting options, call proceed_to_time_processing to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_time_processing",
                    description="Transition to time processing after presenting options.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_time_processing_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_time_processing_node(self) -> NodeConfig:
        """Node to extract the selected time from patient's response.

        Uses main_llm for accurate time extraction with function calling.
        """
        available_times = ["9:00 AM", "10:30 AM", "1:00 PM", "3:30 PM"]

        return NodeConfig(
            name="time_processing",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant. Available times: {', '.join(available_times)}."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the patient's response and extract which time they selected.

When they choose a time, call select_appointment_time with their selection.
Match their response to available times (9:00 AM, 10:30 AM, 1:00 PM, 3:30 PM).

If unclear, ask: "Which of those times works best for you?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="select_appointment_time",
                    description="Patient has selected their preferred appointment time.",
                    properties={
                        "selected_time": {
                            "type": "string",
                            "description": "The time slot the patient selected"
                        }
                    },
                    required=["selected_time"],
                    handler=self._select_time_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_collect_first_name_node(self) -> NodeConfig:
        """Node to collect patient's first name.

        Uses classifier_llm (fast) for conversational intro.
        Transitions to first_name_processing for extraction.
        Resets context with summary to reduce token count.
        """
        return NodeConfig(
            name="collect_first_name",
            role_messages=[{
                "role": "system",
                "content": f"""You are a friendly Virtual Assistant. Appointment: {self.appointment_date} at {self.appointment_time}."""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""Ask for the patient's first name to complete the booking.

Say: "Wonderful! I just need a few details to book your appointment for {self.appointment_date} at {self.appointment_time}. What's your first name?"

After asking, call proceed_to_first_name_processing to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_first_name_processing",
                    description="Transition to first name processing after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_first_name_processing_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET_WITH_SUMMARY,
                summary_prompt="Summarize: service type, appointment date and time. Keep under 20 words."
            )
        )

    def create_first_name_processing_node(self) -> NodeConfig:
        """Node to extract first name from patient's response.

        Uses main_llm for accurate name extraction with function calling.
        """
        return NodeConfig(
            name="first_name_processing",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Extract names accurately."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the patient's response and extract their first name.

When they provide it, call save_first_name with the name.
If they give both first and last name, only extract the first name here.

If unclear, ask: "Could you spell your first name for me?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_first_name",
                    description="Save the patient's first name.",
                    properties={
                        "first_name": {
                            "type": "string",
                            "description": "The patient's first name"
                        }
                    },
                    required=["first_name"],
                    handler=self._save_first_name_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_collect_last_name_node(self) -> NodeConfig:
        """Node to collect patient's last name.

        Uses classifier_llm (fast) for conversational question.
        Transitions to last_name_processing for extraction.
        """
        return NodeConfig(
            name="collect_last_name",
            role_messages=[{
                "role": "system",
                "content": f"""You are a friendly Virtual Assistant. Patient's first name is {self.collected_first_name}."""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""Ask for the patient's last name.

Say: "Great, {self.collected_first_name}! And what's your last name?"

After asking, call proceed_to_last_name_processing to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_last_name_processing",
                    description="Transition to last name processing after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_last_name_processing_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_last_name_processing_node(self) -> NodeConfig:
        """Node to extract last name from patient's response.

        Uses main_llm for accurate name extraction with function calling.
        """
        return NodeConfig(
            name="last_name_processing",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Extract names accurately."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the patient's response and extract their last name.

When they provide it, call save_last_name with the name.

If unclear, ask: "Could you spell your last name for me?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_last_name",
                    description="Save the patient's last name.",
                    properties={
                        "last_name": {
                            "type": "string",
                            "description": "The patient's last name"
                        }
                    },
                    required=["last_name"],
                    handler=self._save_last_name_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_collect_phone_node(self) -> NodeConfig:
        """Node to collect patient's phone number.

        Uses classifier_llm (fast) for conversational question.
        Transitions to phone_processing for extraction.
        """
        return NodeConfig(
            name="collect_phone",
            role_messages=[{
                "role": "system",
                "content": f"""You are a friendly Virtual Assistant. Patient: {self.collected_first_name} {self.collected_last_name}."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Ask for the patient's phone number.

Say: "Perfect! And what's the best phone number to reach you at?"

After asking, call proceed_to_phone_processing to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_phone_processing",
                    description="Transition to phone processing after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_phone_processing_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_phone_processing_node(self) -> NodeConfig:
        """Node to extract phone number from patient's response.

        Uses main_llm for accurate phone extraction with function calling.
        """
        return NodeConfig(
            name="phone_processing",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Extract phone numbers accurately."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the patient's response and extract their phone number.

When they provide it, call save_phone_number with the number.
Accept various formats (with/without area code, dashes, etc.).

If unclear, ask: "Could you repeat your phone number?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_phone_number",
                    description="Save the patient's phone number.",
                    properties={
                        "phone_number": {
                            "type": "string",
                            "description": "The patient's phone number"
                        }
                    },
                    required=["phone_number"],
                    handler=self._save_phone_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_collect_dob_node(self) -> NodeConfig:
        """Node to collect patient's date of birth.

        Uses classifier_llm (fast) for conversational question.
        Transitions to dob_processing for extraction.
        """
        return NodeConfig(
            name="collect_dob",
            role_messages=[{
                "role": "system",
                "content": """You are a friendly Virtual Assistant collecting patient information."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Ask for the patient's date of birth.

Say: "And what's your date of birth?"

After asking, call proceed_to_dob_processing to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_dob_processing",
                    description="Transition to DOB processing after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_dob_processing_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_dob_processing_node(self) -> NodeConfig:
        """Node to extract date of birth from patient's response.

        Uses main_llm for accurate date extraction with function calling.
        """
        return NodeConfig(
            name="dob_processing",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Extract dates of birth accurately."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the patient's response and extract their date of birth.

When they provide it, call save_date_of_birth with the date.
Accept various formats: "March 15 1985", "3/15/85", "03-15-1985", etc.

If unclear, ask: "Could you repeat your date of birth?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_date_of_birth",
                    description="Save the patient's date of birth.",
                    properties={
                        "date_of_birth": {
                            "type": "string",
                            "description": "The patient's date of birth"
                        }
                    },
                    required=["date_of_birth"],
                    handler=self._save_dob_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_collect_email_node(self) -> NodeConfig:
        """Node to collect patient's email address.

        Uses classifier_llm (fast) for conversational question.
        Transitions to email_processing for extraction.
        """
        return NodeConfig(
            name="collect_email",
            role_messages=[{
                "role": "system",
                "content": """You are a friendly Virtual Assistant. Almost done collecting info!"""
            }],
            task_messages=[{
                "role": "system",
                "content": """Ask for the patient's email address.

Say: "Almost done! What's your email address so we can send you a confirmation?"

After asking, call proceed_to_email_processing to continue."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_email_processing",
                    description="Transition to email processing after asking.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_email_processing_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    def create_email_processing_node(self) -> NodeConfig:
        """Node to extract email from patient's response.

        Uses main_llm for accurate email extraction with function calling.
        """
        return NodeConfig(
            name="email_processing",
            role_messages=[{
                "role": "system",
                "content": """You are a Virtual Assistant. Extract email addresses accurately."""
            }],
            task_messages=[{
                "role": "system",
                "content": """Listen to the patient's response and extract their email address.

When they provide it, call save_email with the email.

If unclear or need to confirm spelling, ask: "Could you spell your email address for me?"."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="save_email",
                    description="Save the patient's email address.",
                    properties={
                        "email": {
                            "type": "string",
                            "description": "The patient's email address"
                        }
                    },
                    required=["email"],
                    handler=self._save_email_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }]
        )

    def create_confirmation_node(self) -> NodeConfig:
        """Node to confirm all appointment details.

        Uses main_llm for accuracy - needs to spell back info correctly
        and handle corrections with function calling.
        Resets context with summary since we have all the info we need.
        """
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        service_display = {
            "whitening": "Professional Teeth Whitening",
            "new_patient": "New Patient Appointment",
            "checkup": "Check-up",
            "urgent": "Dental Visit"
        }.get(self.service_type, "appointment")

        return NodeConfig(
            name="confirmation",
            role_messages=[{
                "role": "system",
                "content": f"""You are a Virtual Assistant from {facility_name}. Be accurate when reading back information."""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""Review all the details with the patient.

APPOINTMENT DETAILS TO CONFIRM:
- Service: {service_display}
- Date: {self.appointment_date}
- Time: {self.appointment_time}
- Name: {self.collected_first_name} {self.collected_last_name}
- Phone: {self.collected_phone}
- DOB: {self.collected_dob}
- Email: {self.collected_email}

Say: "Let me confirm everything! I have you down for a {service_display} on {self.appointment_date} at {self.appointment_time}. Your name is {self.collected_first_name} {self.collected_last_name}, phone is {self.collected_phone}, date of birth is {self.collected_dob}, and email is {self.collected_email}. Does everything look correct?"

- If they CONFIRM: call confirm_and_book_appointment
- If they need to CORRECT something: call correct_information with the field and corrected value"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="confirm_and_book_appointment",
                    description="Patient confirms all details are correct - book the appointment.",
                    properties={},
                    required=[],
                    handler=self._confirm_and_book_handler
                ),
                FlowsFunctionSchema(
                    name="correct_information",
                    description="Patient needs to correct some information.",
                    properties={
                        "field_to_correct": {
                            "type": "string",
                            "description": "Which field needs correction (first_name, last_name, phone, dob, email, date, time)"
                        },
                        "corrected_value": {
                            "type": "string",
                            "description": "The corrected value"
                        }
                    },
                    required=["field_to_correct", "corrected_value"],
                    handler=self._correct_information_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_main_llm
            }],
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET
            )
        )

    def create_closing_node(self) -> NodeConfig:
        """Final node after successful booking.

        Uses classifier_llm (fast) for warm closing message.
        Simple end_call function to terminate.
        """
        facility_name = self.patient_data.get('facility_name', 'Demo Clinic Beta')

        service_display = {
            "whitening": "Professional Teeth Whitening",
            "new_patient": "New Patient Appointment",
            "checkup": "Check-up",
            "urgent": "Dental Visit"
        }.get(self.service_type, "appointment")

        return NodeConfig(
            name="closing",
            role_messages=[{
                "role": "system",
                "content": f"""You are a warm Virtual Assistant from {facility_name}. The appointment is booked!"""
            }],
            task_messages=[{
                "role": "system",
                "content": f"""The appointment has been booked! Celebrate with the patient!

Say: "Your appointment is all set! We're so excited to see you on {self.appointment_date} at {self.appointment_time}! You'll receive a confirmation email at {self.collected_email}. Thank you for choosing {facility_name}, {self.collected_first_name}! Have a wonderful day!"

Then call end_call to finish."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the conversation after saying goodbye.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler
                )
            ],
            respond_immediately=True,
            pre_actions=[{
                "type": "function",
                "handler": self._switch_to_classifier_llm
            }]
        )

    # ========== LLM Switching Functions ==========
    # These are placeholder functions for switching between LLMs at different nodes.
    # Use pre_actions in NodeConfig to call these when entering specific nodes.
    #
    # Example usage in a node:
    #   pre_actions=[{
    #       "type": "function",
    #       "handler": self._switch_to_classifier_llm
    #   }]

    async def _switch_to_main_llm(self, action: dict, flow_manager: FlowManager):
        """Switch to the main LLM (e.g., Claude Haiku for complex reasoning)."""
        if self.context_aggregator and self.main_llm:
            await self.context_aggregator.assistant().push_frame(
                ManuallySwitchServiceFrame(service=self.main_llm),
                FrameDirection.UPSTREAM
            )
            logger.info("LLM switched to: main_llm")

    async def _switch_to_classifier_llm(self, action: dict, flow_manager: FlowManager):
        """Switch to the classifier/secondary LLM (e.g., Groq for fast responses)."""
        if self.context_aggregator and self.classifier_llm:
            await self.context_aggregator.assistant().push_frame(
                ManuallySwitchServiceFrame(service=self.classifier_llm),
                FrameDirection.UPSTREAM
            )
            logger.info("LLM switched to: classifier_llm")

    # ========== Transition Handler Functions ==========
    # These handlers transition between conversational (classifier_llm) and
    # processing (main_llm) nodes in the split-node pattern.
    # IMPORTANT: Return empty string "" instead of None to avoid Anthropic API error
    # "tool_use.input: Input should be a valid dictionary"

    async def _proceed_to_patient_type_selection_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from greeting to patient type selection."""
        logger.info("Flow: greeting → patient_type_selection")
        return "", self.create_patient_type_selection_node()

    async def _proceed_to_service_selection_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from new_patient_service to service selection."""
        logger.info("Flow: new_patient_service → new_patient_service_selection")
        return "", self.create_new_patient_service_selection_node()

    async def _proceed_to_reason_selection_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from returning_patient_reason to reason selection."""
        logger.info("Flow: returning_patient_reason → returning_patient_reason_selection")
        return "", self.create_returning_patient_reason_selection_node()

    async def _proceed_to_date_processing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from date_selection to date processing."""
        logger.info("Flow: date_selection → date_processing")
        return "", self.create_date_processing_node()

    async def _proceed_to_time_processing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from time_selection to time processing."""
        logger.info("Flow: time_selection → time_processing")
        return "", self.create_time_processing_node()

    async def _proceed_to_first_name_processing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from collect_first_name to first name processing."""
        logger.info("Flow: collect_first_name → first_name_processing")
        return "", self.create_first_name_processing_node()

    async def _proceed_to_last_name_processing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from collect_last_name to last name processing."""
        logger.info("Flow: collect_last_name → last_name_processing")
        return "", self.create_last_name_processing_node()

    async def _proceed_to_phone_processing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from collect_phone to phone processing."""
        logger.info("Flow: collect_phone → phone_processing")
        return "", self.create_phone_processing_node()

    async def _proceed_to_dob_processing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from collect_dob to DOB processing."""
        logger.info("Flow: collect_dob → dob_processing")
        return "", self.create_dob_processing_node()

    async def _proceed_to_email_processing_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Transition from collect_email to email processing."""
        logger.info("Flow: collect_email → email_processing")
        return "", self.create_email_processing_node()

    # ========== Classification/Processing Handler Functions ==========

    async def _set_new_patient_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Mark as new patient and go to service selection."""
        self.is_new_patient = True
        logger.info("Flow: Patient is NEW")
        return "New patient confirmed.", self.create_new_patient_service_node()

    async def _set_returning_patient_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Mark as returning patient and go to reason selection."""
        self.is_new_patient = False
        logger.info("Flow: Patient is RETURNING")
        return "Returning patient confirmed.", self.create_returning_patient_reason_node()

    async def _select_teeth_whitening_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """New patient wants teeth whitening."""
        self.service_type = "whitening"
        logger.info("Flow: Service selected - Professional Teeth Whitening")
        return "Professional Teeth Whitening service selected.", self.create_date_selection_node()

    async def _select_new_patient_appointment_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """New patient wants general appointment."""
        self.service_type = "new_patient"
        logger.info("Flow: Service selected - New Patient Appointment")
        return "New Patient Appointment selected.", self.create_date_selection_node()

    async def _select_checkup_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Returning patient needs checkup."""
        self.service_type = "checkup"
        logger.info("Flow: Service selected - Regular Check-up")
        return "Regular check-up selected.", self.create_date_selection_node()

    async def _select_pain_issue_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Returning patient has pain/issue."""
        self.service_type = "urgent"
        issue = args.get("issue_description", "dental issue")
        logger.info(f"Flow: Service selected - Urgent (issue: {issue})")
        return f"Noted the issue: {issue}. Let's get you scheduled.", self.create_date_selection_node()

    async def _check_date_availability_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Check availability for requested date."""
        self.appointment_date = args.get("requested_date", "").strip()
        logger.info(f"Flow: Checking availability for {self.appointment_date}")
        # In production, this would check actual availability
        return f"Checked availability for {self.appointment_date}.", self.create_time_selection_node()

    async def _select_time_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Patient selected a time slot."""
        self.appointment_time = args.get("selected_time", "").strip()
        logger.info(f"Flow: Time selected - {self.appointment_time}")
        return f"Time {self.appointment_time} selected.", self.create_collect_first_name_node()

    async def _save_first_name_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save first name and continue."""
        self.collected_first_name = args.get("first_name", "").strip()
        logger.info(f"Flow: First name collected - {self.collected_first_name}")
        return f"First name '{self.collected_first_name}' saved.", self.create_collect_last_name_node()

    async def _save_last_name_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save last name and continue."""
        self.collected_last_name = args.get("last_name", "").strip()
        logger.info(f"Flow: Last name collected - {self.collected_last_name}")
        return f"Last name '{self.collected_last_name}' saved.", self.create_collect_phone_node()

    async def _save_phone_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save phone number and continue."""
        self.collected_phone = args.get("phone_number", "").strip()
        logger.info(f"Flow: Phone collected - {self.collected_phone}")
        return f"Phone number saved.", self.create_collect_dob_node()

    async def _save_dob_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save date of birth and continue."""
        self.collected_dob = args.get("date_of_birth", "").strip()
        logger.info(f"Flow: DOB collected - {self.collected_dob}")
        return f"Date of birth saved.", self.create_collect_email_node()

    async def _save_email_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Save email and go to confirmation."""
        self.collected_email = args.get("email", "").strip()
        logger.info(f"Flow: Email collected - {self.collected_email}")
        return f"Email saved.", self.create_confirmation_node()

    async def _correct_information_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Correct a piece of information."""
        field = args.get("field_to_correct", "").lower()
        value = args.get("corrected_value", "").strip()

        logger.info(f"Flow: Correcting {field} to {value}")

        if field == "first_name":
            self.collected_first_name = value
        elif field == "last_name":
            self.collected_last_name = value
        elif field == "phone":
            self.collected_phone = value
        elif field == "dob":
            self.collected_dob = value
        elif field == "email":
            self.collected_email = value
        elif field == "date":
            self.appointment_date = value
        elif field == "time":
            self.appointment_time = value

        return f"Updated {field} to '{value}'.", self.create_confirmation_node()

    async def _confirm_and_book_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, 'NodeConfig']:
        """Book the appointment in the database."""
        logger.info(f"Flow: Booking appointment for {self.collected_first_name} {self.collected_last_name}")

        try:
            db = get_async_patient_db()
            patient_id = self.patient_data.get('patient_id')

            if patient_id:
                # Update existing patient record
                update_fields = {
                    "first_name": self.collected_first_name,
                    "last_name": self.collected_last_name,
                    "patient_name": f"{self.collected_last_name}, {self.collected_first_name}",
                    "phone_number": self.collected_phone,
                    "date_of_birth": self.collected_dob,
                    "email": self.collected_email,
                    "appointment_date": self.appointment_date,
                    "appointment_time": self.appointment_time,
                    "service_type": self.service_type,
                    "is_new_patient": self.is_new_patient,
                    "call_status": "Completed"
                }

                await db.update_patient(patient_id, update_fields, self.organization_id)
                logger.info(f"Patient record updated: {patient_id}")
            else:
                logger.warning("No patient_id found - appointment saved in memory only")

            return "Appointment booked successfully!", self.create_closing_node()

        except Exception as e:
            logger.error(f"Error booking appointment: {e}")
            return "I apologize, there was an issue. Let me try again.", self.create_confirmation_node()

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, None]:
        """End the call and save transcript."""
        logger.info("Call ended by flow")
        patient_id = self.patient_data.get('patient_id')
        db = get_async_patient_db() if patient_id else None

        try:
            # Save transcript
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            # Update database status to Completed
            if patient_id and db:
                await db.update_call_status(patient_id, "Completed", self.organization_id)
                logger.info(f"Database status updated: Completed (patient_id: {patient_id})")

            # Push EndTaskFrame for graceful shutdown
            if self.context_aggregator:
                await self.context_aggregator.assistant().push_frame(
                    EndTaskFrame(),
                    FrameDirection.UPSTREAM
                )

        except Exception as e:
            import traceback
            logger.error(f"Error in end_call_handler: {traceback.format_exc()}")

            # Update status to Failed on error
            if patient_id and db:
                try:
                    await db.update_call_status(patient_id, "Failed", self.organization_id)
                    logger.info(f"Database status updated: Failed (patient_id: {patient_id})")
                except Exception as db_error:
                    logger.error(f"Failed to update status to Failed: {db_error}")

        return None, None
