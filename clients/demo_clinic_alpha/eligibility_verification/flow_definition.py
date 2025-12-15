from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema, ContextStrategy, ContextStrategyConfig
from loguru import logger
from backend.models import get_async_patient_db
from handlers.transcript import save_transcript_to_db


class EligibilityVerificationFlow:
    """Eligibility verification flow with triage support."""

    # ═══════════════════════════════════════════════════════════════════
    # TRIAGE CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════

    TRIAGE_CLASSIFIER_PROMPT = """You are a call classification system for OUTBOUND insurance verification calls.

HUMAN CONVERSATION (respond "CONVERSATION"):
- Personal greetings: "Hello?", "Hi", "Speaking", "This is [name]"
- Department greetings: "Insurance verification, this is Sarah"
- Interactive responses: "Who is this?", "How can I help you?"
- Natural speech with pauses and informal tone

IVR SYSTEM (respond "IVR"):
- Menu options: "Press 1 for claims", "Press 2 for eligibility"
- Automated instructions: "Please enter your provider NPI"
- System prompts: "Thank you for calling [insurance company]"
- Hold messages: "Please hold while we transfer you"

VOICEMAIL SYSTEM (respond "VOICEMAIL"):
- Voicemail greetings: "You've reached the claims department, please leave a message"
- After-hours messages: "Our office is currently closed"
- Carrier messages: "The number you have dialed is not available"
- Mailbox messages: "This mailbox is full"

Output exactly one classification word: CONVERSATION, IVR, or VOICEMAIL."""

    IVR_NAVIGATION_GOAL = """Navigate to speak with a representative who can verify eligibility and benefits.

MEMBER INFORMATION (provide exactly as shown when asked):
- Member ID: {insurance_member_id}
- Date of Birth: {date_of_birth}
- Provider NPI: {provider_npi}

NAVIGATION INSTRUCTIONS:
- When asked if you're a member or provider: Say "Health care professional"
- When asked why you're calling: Say "Benefits" or "Eligibility"
- When asked for benefit type: Say the type of service being verified
- When offered menu options: Choose "eligibility", "benefits", "provider services", or "speak to representative"
- When asked to confirm information: Say "Yes" or "Correct"
- When offered surveys: Say "No, thank you"
- When put on hold: Say "Sure" or "Thank you"

Goal: Reach a human representative who can verify coverage details."""

    VOICEMAIL_MESSAGE_TEMPLATE = """Hi, this is {caller_name}, calling from {facility_name} regarding eligibility verification for a patient. Please call us back at your earliest convenience. Thank you."""

    def __init__(self, patient_data: Dict[str, Any], flow_manager: FlowManager = None,
                 main_llm=None, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None, cold_transfer_config: Dict[str, Any] = None):
        self.flow_manager = flow_manager
        self.main_llm = main_llm
        self.classifier_llm = classifier_llm
        self.context_aggregator = context_aggregator
        self.transport = transport
        self.pipeline = pipeline
        self.organization_id = organization_id
        self.cold_transfer_config = cold_transfer_config or {}
        # Store patient_data for later initialization when flow_manager is set
        self._patient_data = patient_data

    def get_triage_config(self) -> dict:
        """Return triage configuration for this flow.

        Called by PipelineFactory to configure TriageDetector and IVRNavigationProcessor.
        Uses patient_data directly since flow_manager may not be set yet.
        """
        return {
            "classifier_prompt": self.TRIAGE_CLASSIFIER_PROMPT,
            "ivr_navigation_goal": self.IVR_NAVIGATION_GOAL.format(
                insurance_member_id=self._patient_data.get("insurance_member_id", ""),
                date_of_birth=self._patient_data.get("date_of_birth", ""),
                provider_npi=self._patient_data.get("provider_npi", ""),
            ),
            "voicemail_message": self.VOICEMAIL_MESSAGE_TEMPLATE.format(
                caller_name=self._patient_data.get("caller_name", "a representative"),
                facility_name=self._patient_data.get("facility_name", "our facility"),
            ),
        }

    def _init_flow_state(self):
        """Initialize flow_manager state with patient data. Called after flow_manager is set."""
        if not self.flow_manager:
            return

        # Patient identification (5 fields)
        self.flow_manager.state["patient_id"] = self._patient_data.get("patient_id")
        self.flow_manager.state["patient_name"] = self._patient_data.get("patient_name", "")
        self.flow_manager.state["date_of_birth"] = self._patient_data.get("date_of_birth", "")
        self.flow_manager.state["insurance_member_id"] = self._patient_data.get("insurance_member_id", "")
        self.flow_manager.state["insurance_company_name"] = self._patient_data.get("insurance_company_name", "")
        self.flow_manager.state["insurance_phone"] = self._patient_data.get("insurance_phone", "")

        # Caller/Provider information (7 fields)
        self.flow_manager.state["caller_name"] = self._patient_data.get("caller_name", "")
        self.flow_manager.state["caller_last_initial"] = self._patient_data.get("caller_last_initial", "")
        self.flow_manager.state["facility_name"] = self._patient_data.get("facility_name", "")
        self.flow_manager.state["tax_id"] = self._patient_data.get("tax_id", "")
        self.flow_manager.state["provider_name"] = self._patient_data.get("provider_name", "")
        self.flow_manager.state["provider_npi"] = self._patient_data.get("provider_npi", "")
        self.flow_manager.state["provider_call_back_phone"] = self._patient_data.get("provider_call_back_phone", "")

        # Service information (3 fields)
        self.flow_manager.state["cpt_code"] = self._patient_data.get("cpt_code", "")
        self.flow_manager.state["place_of_service"] = self._patient_data.get("place_of_service", "")
        self.flow_manager.state["date_of_service"] = self._patient_data.get("date_of_service", "")

    def _get_global_instructions(self) -> str:
        state = self.flow_manager.state
        facility = state.get("facility_name", "")
        caller_name = state.get("caller_name", "")
        caller_initial = state.get("caller_last_initial", "")

        return f"""You are a Virtual Assistant from {facility}, calling to verify insurance eligibility and benefits.

# Voice Conversation Style
You are on a phone call with an insurance representative. Your responses will be converted to speech:
- Speak naturally and professionally, like a healthcare worker on a routine verification call
- Keep responses concise - answer questions directly without over-explaining
- Use natural acknowledgments: "Got it", "Thank you", "I'll wait", "Sure thing"
- When spelling out IDs, say each character clearly
- NEVER use bullet points, numbered lists, or markdown formatting

# Your Identity
- Calling on behalf of: {facility}
- Caller name: {caller_name} {caller_initial}.
- If asked for your full last name, say you can only provide the initial

# Patient & Insurance Information (use ONLY this data - never invent details)
- Patient Name: {state.get("patient_name")}
- Date of Birth: {state.get("date_of_birth")}
- Member ID: {state.get("insurance_member_id")}
- Insurance Company: {state.get("insurance_company_name")}

# Provider Information
- Facility: {facility}
- Tax ID: {state.get("tax_id")}
- Provider Name: {state.get("provider_name")}
- Provider NPI: {state.get("provider_npi")}
- Callback Phone: {state.get("provider_call_back_phone")}

# Service Information
- CPT Code: {state.get("cpt_code")}
- Place of Service: {state.get("place_of_service")}
- Date of Service: {state.get("date_of_service") or "not yet determined"}

# Guardrails
- ONLY provide information listed above. Never guess or invent details. This step is important.
- If asked for information you don't have, say: "I don't have that information available."
- If asked about date of service and it's not determined, say so and ask if they can use today's date.
- If the representative seems uncomfortable with AI, offer to transfer them to a manager.
- Stay on topic: eligibility verification only.
- NEVER fabricate data: Do not invent reference numbers, amounts, dates, or any other information.
- NEVER record individual amounts as family amounts or vice versa.
- After saying goodbye, do not speak again - end the call immediately."""

    def create_greeting_node_after_ivr_completed(self) -> NodeConfig:
        return NodeConfig(
            name="greeting_after_ivr",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": """A representative answered after IVR navigation. They will likely ask identification questions.

Answer their questions naturally using your identity and provider information. Common questions include your name, facility, tax ID, and the member's name.

When they ask how they can help or are ready to assist, call proceed_to_plan_info."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_plan_info",
                    description="Move to plan info questions after identification is complete.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_plan_info_handler
                )
            ],
            respond_immediately=False,
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET
            )
        )

    def create_greeting_node_without_ivr(self) -> NodeConfig:
        return NodeConfig(
            name="greeting_without_ivr",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": """A human answered directly. Introduce yourself: "Hi, this is [your name] calling from [facility] about eligibility verification for [patient name]."

Answer their identification questions naturally (name, facility, tax ID, member name/DOB).

CRITICAL: As soon as the rep indicates they can help OR starts giving ANY info, you MUST call proceed_to_plan_info immediately.

CAPTURE ALL VOLUNTEERED INFO in the function call:
- Plan info: network status, plan type, effective/term dates
- CPT coverage: covered status, copay, coinsurance, deductible applies, prior auth (PA), telehealth
- Accumulators: deductible amounts, OOP max amounts
- Reference number

Example: If rep says "They're in network, POS plan, code is covered, $75 copay, 20% coinsurance, PA required" - capture ALL of that in proceed_to_plan_info."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_plan_info",
                    description="Move to plan info. Include ANY info the rep already volunteered - plan info, CPT coverage, accumulators, or reference number.",
                    properties={
                        # Plan info
                        "network_status": {"type": "string", "enum": ["In-Network", "Out-of-Network", "Unknown"], "description": "If mentioned"},
                        "plan_type": {"type": "string", "description": "PPO, HMO, POS, EPO, HDHP if mentioned"},
                        "plan_effective_date": {"type": "string", "description": "MM/DD/YYYY if mentioned"},
                        "plan_term_date": {"type": "string", "description": "'None' or MM/DD/YYYY if mentioned"},
                        # CPT coverage
                        "cpt_covered": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If rep said covered/not covered"},
                        "copay_amount": {"type": "string", "description": "'$50', 'None' if mentioned"},
                        "coinsurance_percent": {"type": "string", "description": "'20%', 'None' if mentioned"},
                        "deductible_applies": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If mentioned"},
                        "prior_auth_required": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If PA/prior auth mentioned"},
                        "telehealth_covered": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If mentioned"},
                        # Accumulators
                        "deductible_family": {"type": "string", "description": "Family deductible amount if mentioned"},
                        "deductible_family_met": {"type": "string", "description": "Amount met if mentioned"},
                        "oop_max_family": {"type": "string", "description": "Family OOP max if mentioned"},
                        "oop_max_family_met": {"type": "string", "description": "Amount met if mentioned"},
                        "reference_number": {"type": "string", "description": "Reference/confirmation number if mentioned"}
                    },
                    required=[],
                    handler=self._proceed_to_plan_info_handler
                )
            ],
            respond_immediately=True
        )

    def create_plan_info_node(self) -> NodeConfig:
        """Gather basic plan information: network status, plan type, effective date, term date."""
        state = self.flow_manager.state
        facility = state.get("facility_name", "")

        # Check what's already captured
        has_network = bool(state.get("network_status"))
        has_plan_type = bool(state.get("plan_type"))
        has_effective = bool(state.get("plan_effective_date"))
        has_term = bool(state.get("plan_term_date"))

        # Build list of missing fields
        missing = []
        if not has_network:
            missing.append(f'Network status: "Is {facility} participating in the network?"')
        if not has_plan_type:
            missing.append('Plan type: "What type of plan is this?"')
        if not has_effective:
            missing.append('Effective date: "What is the effective date?"')
        if not has_term:
            missing.append('Term date: "Is there a term date?"')

        # Build state summary
        captured = []
        if has_network:
            captured.append(f"network_status: {state.get('network_status')}")
        if has_plan_type:
            captured.append(f"plan_type: {state.get('plan_type')}")
        if has_effective:
            captured.append(f"effective_date: {state.get('plan_effective_date')}")
        if has_term:
            captured.append(f"term_date: {state.get('plan_term_date')}")

        captured_text = ", ".join(captured) if captured else "none"
        missing_text = "\n".join(f"- {m}" for m in missing) if missing else "ALL CAPTURED - call proceed_to_cpt_coverage NOW"

        return NodeConfig(
            name="plan_info",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Gather plan information, then move to CPT coverage questions.

# Already Captured
{captured_text}

# Still Need (Plan Info)
{missing_text}

# Instructions
- If all 4 plan info fields are captured, call proceed_to_cpt_coverage IMMEDIATELY
- Only ask about MISSING plan info fields - never re-ask what's already captured
- When rep answers, record via the appropriate function
- If rep asks about CPT code or volunteers coverage info, call proceed_to_cpt_coverage

# CRITICAL: DO NOT END THE CALL
- When rep says "anything else?" - if plan info fields are missing, ask for them
- If all plan info is captured, call proceed_to_cpt_coverage - DO NOT say goodbye
- DO NOT say "that covers everything" or thank them until the ENTIRE call is done
- You still need CPT coverage details, accumulators, and reference number after this

# Capture Volunteered Info
Include any CPT coverage, accumulators, or reference info in proceed_to_cpt_coverage call.

# Data Normalization
- Network: "in network" → "In-Network", "non-par" → "Out-of-Network"
- Plan type: PPO, HMO, POS, EPO, HDHP
- Dates: MM/DD/YYYY format
- PA/prior auth → prior_auth_required: "Yes" """
            }],
            functions=[
                FlowsFunctionSchema(
                    name="record_network_status",
                    description="""Record network participation status.

WHEN TO USE: After rep confirms whether facility is in-network.
VALID VALUES: "In-Network", "Out-of-Network", "Unknown"

EXAMPLES:
- "yes, in network" → "In-Network"
- "this facility is participating" → "In-Network"
- "not in network" → "Out-of-Network" """,
                    properties={
                        "status": {
                            "type": "string",
                            "enum": ["In-Network", "Out-of-Network", "Unknown"],
                            "description": "Network status"
                        }
                    },
                    required=["status"],
                    handler=self._record_network_status_handler
                ),
                FlowsFunctionSchema(
                    name="record_plan_type",
                    description="""Record the plan type.

WHEN TO USE: After rep states the plan type.
VALID VALUES: "PPO", "HMO", "POS", "EPO", "HDHP", "Unknown"

EXAMPLES:
- "It's a POS plan" → "POS"
- "This is an HMO" → "HMO" """,
                    properties={
                        "plan_type": {
                            "type": "string",
                            "description": "Plan type: PPO, HMO, POS, EPO, HDHP, or Unknown"
                        }
                    },
                    required=["plan_type"],
                    handler=self._record_plan_type_handler
                ),
                FlowsFunctionSchema(
                    name="record_plan_effective_date",
                    description="""Record plan effective date.

WHEN TO USE: After rep provides the effective date.
FORMAT: MM/DD/YYYY (e.g., "01/01/2025")

EXAMPLES:
- "January first twenty twenty five" → "01/01/2025"
- "oh one oh one two thousand twenty five" → "01/01/2025" """,
                    properties={
                        "date": {
                            "type": "string",
                            "description": "Effective date in MM/DD/YYYY format"
                        }
                    },
                    required=["date"],
                    handler=self._record_plan_effective_date_handler
                ),
                FlowsFunctionSchema(
                    name="record_plan_term_date",
                    description="""Record plan termination date.

WHEN TO USE: After rep provides term date or confirms there is none.
FORMAT: MM/DD/YYYY or "None"

EXAMPLES:
- "December thirty first twenty twenty five" → "12/31/2025"
- "No future termination date" → "None"
- "None on file" → "None" """,
                    properties={
                        "date": {
                            "type": "string",
                            "description": "Term date in MM/DD/YYYY format, or 'None' if no termination date"
                        }
                    },
                    required=["date"],
                    handler=self._record_plan_term_date_handler
                ),
                FlowsFunctionSchema(
                    name="proceed_to_cpt_coverage",
                    description="Move to CPT coverage. Include ANY info the rep already volunteered.",
                    properties={
                        # CPT coverage
                        "cpt_covered": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If rep said covered/not covered"},
                        "copay_amount": {"type": "string", "description": "'$50', 'None' if mentioned"},
                        "coinsurance_percent": {"type": "string", "description": "'20%', 'None' if mentioned"},
                        "deductible_applies": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If mentioned"},
                        "prior_auth_required": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If PA/prior auth mentioned"},
                        "telehealth_covered": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If mentioned"},
                        # Accumulators
                        "deductible_family": {"type": "string", "description": "Family deductible amount if mentioned"},
                        "deductible_family_met": {"type": "string", "description": "Amount met if mentioned"},
                        "oop_max_family": {"type": "string", "description": "Family OOP max if mentioned"},
                        "oop_max_family_met": {"type": "string", "description": "Amount met if mentioned"},
                        "reference_number": {"type": "string", "description": "Reference/confirmation number if mentioned"}
                    },
                    required=[],
                    handler=self._proceed_to_cpt_coverage_handler
                )
            ],
            respond_immediately=True
        )

    def create_cpt_coverage_node(self) -> NodeConfig:
        """Gather CPT coverage details: coverage status, copay, coinsurance, deductible, prior auth, telehealth."""
        state = self.flow_manager.state
        cpt_code = state.get("cpt_code", "")
        place_of_service = state.get("place_of_service", "")
        date_of_service = state.get("date_of_service", "")

        # Check what's already captured
        cpt_fields = {
            "cpt_covered": ("Coverage status", f'"Does the patient have coverage for CPT code {cpt_code}?"'),
            "copay_amount": ("Copay", '"What is the copay?"'),
            "coinsurance_percent": ("Coinsurance", '"Is there a coinsurance?"'),
            "deductible_applies": ("Deductible applies", '"Does the deductible apply?"'),
            "prior_auth_required": ("Prior auth", '"Is prior authorization required?"'),
            "telehealth_covered": ("Telehealth", '"Is telehealth covered for this service?"'),
        }

        captured = []
        missing = []
        for field, (label, question) in cpt_fields.items():
            value = state.get(field)
            if value:
                captured.append(f"{field}: {value}")
            else:
                missing.append(f"- {label}: {question}")

        captured_text = ", ".join(captured) if captured else "none"
        missing_text = "\n".join(missing) if missing else "ALL CAPTURED - call proceed_to_accumulators NOW"

        return NodeConfig(
            name="cpt_coverage",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Verify coverage details for CPT code {cpt_code}.

# Already Captured
{captured_text}

# Still Need
{missing_text}

# CRITICAL: If CPT is NOT COVERED
If the rep says the CPT code is "not covered", "excluded", "not a covered benefit", or similar:
- Record cpt_covered as "No"
- DO NOT ask about copay, coinsurance, deductible, prior auth, or telehealth - these are irrelevant for non-covered services
- Proceed directly to accumulators (you still need deductible/OOP info and reference number)

# Capture ALL Info From Each Response
Insurance reps often provide multiple pieces of information in one response. Listen carefully and extract EVERYTHING they mention:
- "covered" / "valid and billable" → cpt_covered: Yes
- "not covered" / "excluded" / "plan exclusion" → cpt_covered: No (then skip to accumulators)
- "no copay" / "$X copay" → copay_amount
- "X% coinsurance" → coinsurance_percent
- "deductible applies" / "ded applies" → deductible_applies: Yes
- "PA required" / "need prior auth" → prior_auth_required: Yes
- "telehealth available/not available" → telehealth_covered

NEVER re-ask for information the rep just provided in their response. This step is important.

# Instructions
- If cpt_covered = "No", call proceed_to_accumulators IMMEDIATELY (skip other CPT questions)
- If all 6 CPT coverage fields are captured, call proceed_to_accumulators IMMEDIATELY
- Only ask about fields NOT yet mentioned by the rep
- When rep answers, record ALL mentioned fields via function calls
- If rep volunteers accumulator info or reference number, include in proceed_to_accumulators

# CRITICAL: DO NOT END THE CALL
- When rep says "anything else?" - YES, you need accumulators and reference! Ask for them.
- DO NOT say goodbye, thank you, or end the conversation from this node
- You MUST call proceed_to_accumulators to continue
- The call is NOT complete until you have accumulators AND reference number

# Handling Rep Questions
- Date of service: "{date_of_service or "Not yet determined. Is it okay to use today's date?"}"
- Place of service: "{place_of_service}"

# Data Normalization
- Amounts: "$50", "None" | Percentages: "20%", "None"
- Yes/No: "required"/"applies" → "Yes", "not required"/"doesn't apply" → "No"
- PA/prior auth → "Yes" """
            }],
            functions=[
                FlowsFunctionSchema(
                    name="record_cpt_covered",
                    description="""Record whether CPT code is covered.

WHEN TO USE: After rep confirms coverage status.
VALID VALUES: "Yes", "No", "Unknown"

EXAMPLES:
- "valid and billable" → "Yes"
- "covered" → "Yes"
- "not covered under this plan" → "No" """,
                    properties={
                        "covered": {
                            "type": "string",
                            "enum": ["Yes", "No", "Unknown"],
                            "description": "Coverage status"
                        }
                    },
                    required=["covered"],
                    handler=self._record_cpt_covered_handler
                ),
                FlowsFunctionSchema(
                    name="record_copay",
                    description="""Record copay amount.

WHEN TO USE: After rep provides copay information.
FORMAT: Dollar amount (e.g., "$50"), "None", or "Unknown"

EXAMPLES:
- "fifty dollars per service" → "$50"
- "twenty five dollar copay" → "$25"
- "no copay" → "None"
- "copay does not apply" → "None" """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Copay amount (e.g., '$50', 'None', 'Unknown')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_copay_handler
                ),
                FlowsFunctionSchema(
                    name="record_coinsurance",
                    description="""Record coinsurance percentage.

WHEN TO USE: After rep provides coinsurance information.
FORMAT: Percentage (e.g., "20%"), "None", or "Unknown"

EXAMPLES:
- "twenty percent coinsurance" → "20%"
- "zero percent" → "0%"
- "no coinsurance" → "None"
- "you pay 80 percent, insurance pays 20" → "80%" (patient responsibility) """,
                    properties={
                        "percent": {
                            "type": "string",
                            "description": "Coinsurance percentage (e.g., '20%', 'None', 'Unknown')"
                        }
                    },
                    required=["percent"],
                    handler=self._record_coinsurance_handler
                ),
                FlowsFunctionSchema(
                    name="record_deductible_applies",
                    description="""Record whether deductible applies to this service.

WHEN TO USE: After rep confirms deductible applicability.
VALID VALUES: "Yes", "No", "Unknown"

EXAMPLES:
- "deductible applies" → "Yes"
- "annual deductible does not apply" → "No"
- "after meeting your deductible" → "Yes" """,
                    properties={
                        "applies": {
                            "type": "string",
                            "enum": ["Yes", "No", "Unknown"],
                            "description": "Whether deductible applies"
                        }
                    },
                    required=["applies"],
                    handler=self._record_deductible_applies_handler
                ),
                FlowsFunctionSchema(
                    name="record_prior_auth_required",
                    description="""Record whether prior authorization is required.

WHEN TO USE: After rep confirms prior auth requirement.
VALID VALUES: "Yes", "No", "Unknown"

EXAMPLES:
- "prior authorization is required" → "Yes"
- "prior auth is not required" → "No"
- "you'll need to get approval first" → "Yes" """,
                    properties={
                        "required": {
                            "type": "string",
                            "enum": ["Yes", "No", "Unknown"],
                            "description": "Whether prior auth is required"
                        }
                    },
                    required=["required"],
                    handler=self._record_prior_auth_required_handler
                ),
                FlowsFunctionSchema(
                    name="record_telehealth_covered",
                    description="""Record whether telehealth is covered.

WHEN TO USE: After rep confirms telehealth coverage.
VALID VALUES: "Yes", "No", "Unknown"

EXAMPLES:
- "telehealth is covered" → "Yes"
- "same benefit as in-person" → "Yes"
- "telehealth not available for this service" → "No" """,
                    properties={
                        "covered": {
                            "type": "string",
                            "enum": ["Yes", "No", "Unknown"],
                            "description": "Whether telehealth is covered"
                        }
                    },
                    required=["covered"],
                    handler=self._record_telehealth_covered_handler
                ),
                FlowsFunctionSchema(
                    name="proceed_to_accumulators",
                    description="Move to accumulators. Include ANY info the rep already volunteered.",
                    properties={
                        # Accumulators
                        "deductible_individual": {"type": "string", "description": "Individual deductible if mentioned"},
                        "deductible_individual_met": {"type": "string", "description": "Amount met if mentioned"},
                        "deductible_family": {"type": "string", "description": "Family deductible if mentioned"},
                        "deductible_family_met": {"type": "string", "description": "Amount met if mentioned"},
                        "oop_max_individual": {"type": "string", "description": "Individual OOP max if mentioned"},
                        "oop_max_individual_met": {"type": "string", "description": "Amount met if mentioned"},
                        "oop_max_family": {"type": "string", "description": "Family OOP max if mentioned"},
                        "oop_max_family_met": {"type": "string", "description": "Amount met if mentioned"},
                        "reference_number": {"type": "string", "description": "Reference/confirmation number if mentioned"}
                    },
                    required=[],
                    handler=self._proceed_to_accumulators_handler
                )
            ],
            respond_immediately=True
        )

    def create_accumulators_node(self) -> NodeConfig:
        """Gather deductible and out-of-pocket maximum information, then get a reference number."""
        state = self.flow_manager.state

        # Check what's already captured - focus on family accumulators + reference
        acc_fields = {
            "deductible_family": ("Family deductible", '"What is the family deductible amount?"'),
            "deductible_family_met": ("Family deductible met", '"How much of the family deductible has been met?"'),
            "oop_max_family": ("Family OOP max", '"What is the family out-of-pocket maximum?"'),
            "oop_max_family_met": ("Family OOP met", '"How much of the out-of-pocket maximum has been met?"'),
            "reference_number": ("Reference number", '"May I have a reference number for this call?"'),
        }

        captured = []
        missing = []
        for field, (label, question) in acc_fields.items():
            value = state.get(field)
            if value:
                captured.append(f"{field}: {value}")
            else:
                missing.append(f"- {label}: {question}")

        captured_text = ", ".join(captured) if captured else "none"
        missing_text = "\n".join(missing) if missing else "ALL CAPTURED - call proceed_to_closing NOW"

        return NodeConfig(
            name="accumulators",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Gather deductible and out-of-pocket maximum information, then get a reference number.

# Already Captured
{captured_text}

# Still Need
{missing_text}

# CRITICAL: Individual vs Family Accumulators
Listen carefully to whether the rep says "individual" or "family":
- "individual" / "member" / "single" / "subscriber" → use record_deductible_individual, record_oop_max_individual
- "family" / "household" → use record_deductible_family, record_oop_max_family
- "non-par" / "out-of-network" / "OON" → note this is OON accumulator (still individual or family)

NEVER record individual amounts to family fields or vice versa. This step is important.

If rep ONLY provides individual OR family amounts, ask: "Do you have the [family/individual] amounts as well?"

Example:
- Rep says "The individual non-par deductible is $2,500" → record_deductible_individual("$2,500"), then ask about family
- Rep says "Family deductible is $1,000" → record_deductible_family("$1,000")

# Instructions
- Only ask about MISSING fields - never re-ask what's already captured
- When rep answers, record via the appropriate function

# CRITICAL: After Recording Reference Number
When you record the reference number with record_reference_number, you MUST ALSO call proceed_to_closing in the SAME response. Do both function calls together. This step is important.

# DO NOT say goodbye or thank you without calling proceed_to_closing first
- When rep says "anything else?" - if you still need fields above, ask for them
- If you have the reference number, call proceed_to_closing
- NEVER say "thank you" or "goodbye" from this node - use proceed_to_closing instead

# CRITICAL: Reference Number Rules
- You MUST ask for a reference number: "May I have a reference number for this call?"
- ONLY record a reference number AFTER the rep explicitly provides one
- NEVER use the member ID as a reference number - they are DIFFERENT things
- NEVER guess, invent, or fabricate a reference number
- If the rep says "anything else?" but you don't have a reference number yet, ask for one

WRONG: Recording member ID "CIG334455667" as reference number
RIGHT: Ask "May I have a reference number?" and wait for rep to provide one like "CIG-2025-1234"

# Handling Corrections
If the rep says they need to CORRECT something from earlier:
- Acknowledge: "I'll update that, thank you for the correction"
- Call record_correction ONCE with the field name and corrected value
- DO NOT call record_correction multiple times - one call is sufficient
- Continue with the conversation after recording the correction

# Data Normalization
- Amounts: "five hundred" → "$500", "six thousand" → "$6,000"
- Cents: "eleven seventy point seven four" → "$1,170.74"
- "fully met" → deductible_family_met = deductible_family amount"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="record_deductible_individual",
                    description="""Record INDIVIDUAL deductible amount. Use ONLY for individual/member/single/subscriber deductibles.

WHEN TO USE: Rep explicitly says "individual", "member", "single", or "subscriber" deductible.
DO NOT USE: When rep says "family" or "household" - use record_deductible_family instead.
FORMAT: Dollar amount (e.g., "$500") or "N/A"

EXAMPLES:
- "individual deductible is two hundred fifty" → "$250" ✓
- "the member deductible is five hundred" → "$500" ✓
- "family deductible is one thousand" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Individual deductible amount (e.g., '$500', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_deductible_individual_handler
                ),
                FlowsFunctionSchema(
                    name="record_deductible_individual_met",
                    description="""Record how much of individual deductible has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Dollar amount (e.g., "$200")

EXAMPLES:
- "two hundred of the deductible met" → "$200"
- "fully met" → Use the deductible amount
- "nothing met yet" → "$0" """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of individual deductible met (e.g., '$200')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_deductible_individual_met_handler
                ),
                FlowsFunctionSchema(
                    name="record_deductible_family",
                    description="""Record FAMILY deductible amount. Use ONLY for family/household deductibles.

WHEN TO USE: Rep explicitly says "family" or "household" deductible.
DO NOT USE: When rep says "individual", "member", or "single" - use record_deductible_individual instead.
FORMAT: Dollar amount (e.g., "$500") or "N/A"

EXAMPLES:
- "family deductible is five hundred" → "$500" ✓
- "household deductible is one thousand" → "$1,000" ✓
- "individual deductible is two fifty" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Family deductible amount (e.g., '$500', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_deductible_family_handler
                ),
                FlowsFunctionSchema(
                    name="record_deductible_family_met",
                    description="""Record how much of family deductible has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Dollar amount (e.g., "$500")

EXAMPLES:
- "fully met" or "satisfied" → Use the deductible amount
- "five hundred met" → "$500"
- "nothing applied yet" → "$0" """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of family deductible met (e.g., '$500')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_deductible_family_met_handler
                ),
                FlowsFunctionSchema(
                    name="record_oop_max_individual",
                    description="""Record INDIVIDUAL out-of-pocket maximum. Use ONLY for individual/member/single OOP max.

WHEN TO USE: Rep explicitly says "individual", "member", or "single" OOP max.
DO NOT USE: When rep says "family" or "household" - use record_oop_max_family instead.
FORMAT: Dollar amount (e.g., "$3,000") or "N/A"

EXAMPLES:
- "individual out of pocket max is three thousand" → "$3,000" ✓
- "family OOP max is six thousand" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Individual OOP maximum (e.g., '$3,000', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_oop_max_individual_handler
                ),
                FlowsFunctionSchema(
                    name="record_oop_max_individual_met",
                    description="""Record how much of individual OOP max has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Dollar amount (e.g., "$1,500") """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of individual OOP max met (e.g., '$1,500')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_oop_max_individual_met_handler
                ),
                FlowsFunctionSchema(
                    name="record_oop_max_family",
                    description="""Record FAMILY out-of-pocket maximum. Use ONLY for family/household OOP max.

WHEN TO USE: Rep explicitly says "family" or "household" OOP max.
DO NOT USE: When rep says "individual", "member", or "single" - use record_oop_max_individual instead.
FORMAT: Dollar amount (e.g., "$6,000") or "N/A"

EXAMPLES:
- "family out of pocket max is six thousand" → "$6,000" ✓
- "household OOP max is eight thousand" → "$8,000" ✓
- "individual OOP is three thousand" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Family OOP maximum (e.g., '$6,000', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_oop_max_family_handler
                ),
                FlowsFunctionSchema(
                    name="record_oop_max_family_met",
                    description="""Record how much of family OOP max has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Dollar amount (e.g., "$1,170.74")

EXAMPLES:
- "one thousand one hundred seventy dollars and seventy four cents" → "$1,170.74"
- "about twelve hundred" → Ask for exact amount, then record """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of family OOP max met (e.g., '$1,170.74')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_oop_max_family_met_handler
                ),
                FlowsFunctionSchema(
                    name="record_allowed_amount",
                    description="""Record allowed amount if rep mentions it.

WHEN TO USE: If rep provides an allowed/approved amount for the service.
FORMAT: Dollar amount (e.g., "$150") or "Unknown" """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Allowed amount (e.g., '$150', 'Unknown')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_allowed_amount_handler
                ),
                FlowsFunctionSchema(
                    name="record_reference_number",
                    description="""Record call reference number ONLY when the rep EXPLICITLY provides one.

WHEN TO USE: ONLY after rep says something like "your reference number is..." or "confirmation number..."
NEVER USE:
- If rep hasn't explicitly given a reference number
- NEVER use the member ID as a reference number - they are DIFFERENT things
- NEVER guess or fabricate a reference number

If you don't have a reference number, ASK: "May I have a reference number for this call?"

WRONG: Using member ID "CIG334455667" as reference - this is NOT a reference number
RIGHT: "CIG-2025-1234" provided explicitly by rep

EXAMPLES:
- Rep says "Your reference is ABC123456" → "ABC123456" ✓
- Rep says "Reference number UHC-2025-789456" → "UHC-2025-789456" ✓
- Rep hasn't given a reference → DO NOT CALL THIS FUNCTION, ask for one first """,
                    properties={
                        "reference_number": {
                            "type": "string",
                            "description": "Reference number exactly as stated"
                        }
                    },
                    required=["reference_number"],
                    handler=self._record_reference_number_handler
                ),
                FlowsFunctionSchema(
                    name="record_rep_name",
                    description="""Record representative's name.

WHEN TO USE: If you learn the rep's name during the call.
NOTE: May already be known from greeting.

EXAMPLES:
- "This is Eliza" → "Eliza"
- "My name is Sarah" → "Sarah" """,
                    properties={
                        "name": {
                            "type": "string",
                            "description": "Representative's name"
                        }
                    },
                    required=["name"],
                    handler=self._record_rep_name_handler
                ),
                FlowsFunctionSchema(
                    name="record_correction",
                    description="""Update ANY previously recorded field when rep CORRECTS a value.

WHEN TO USE: ONLY when rep explicitly says they need to correct something from earlier.
Example: "I gave you the wrong copay. It should be fifty dollars, not twenty-five."
Example: "Actually, the coinsurance is twenty percent, not fifteen."

Valid field names: copay_amount, coinsurance_percent, deductible_applies, prior_auth_required,
telehealth_covered, network_status, plan_type, deductible_individual, deductible_family,
oop_max_individual, oop_max_family

IMPORTANT: Only call this function ONCE per correction. Do not repeat.""",
                    properties={
                        "field_name": {
                            "type": "string",
                            "description": "The field to correct (e.g., 'copay_amount', 'coinsurance_percent')"
                        },
                        "corrected_value": {
                            "type": "string",
                            "description": "The CORRECTED value (e.g., '$50', '20%')"
                        }
                    },
                    required=["field_name", "corrected_value"],
                    handler=self._record_correction_handler
                ),
                FlowsFunctionSchema(
                    name="proceed_to_closing",
                    description="Move to closing. Include reference number if just provided.",
                    properties={
                        "reference_number": {"type": "string", "description": "Reference/confirmation number if just mentioned"}
                    },
                    required=[],
                    handler=self._proceed_to_closing_handler
                )
            ],
            respond_immediately=True,
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET
            )
        )

    def create_closing_node(self) -> NodeConfig:
        """Closing node to thank the rep and end the call."""
        return NodeConfig(
            name="closing",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": """# Goal

Thank the representative and end the call.

# CRITICAL: You MUST call end_call

Every response in this node MUST include a call to end_call. This step is important.
- First response: Say thank you, then call end_call
- If you speak at all: You must also call end_call in the same turn

# Instructions

1. Say: "Thank you for your help. Goodbye!"
2. Call end_call in the SAME turn - do not wait for a response

# NEVER DO THIS:
- Do NOT say goodbye and then wait for a response
- Do NOT respond after saying goodbye
- Do NOT say goodbye more than once

# Recording Additional Notes

If the rep already mentioned important info not captured elsewhere, record with record_additional_notes in the same turn as end_call."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="record_additional_notes",
                    description="""Record any additional important information from the rep.

WHEN TO USE: If rep mentions special instructions, limitations, or other important details not covered by other fields.
FORMAT: Free text, exactly as stated.

EXAMPLES:
- "Coverage is limited to 6 visits per year" → Record this limitation
- "Make sure to submit within 90 days" → Record this instruction""",
                    properties={
                        "notes": {
                            "type": "string",
                            "description": "Additional notes or special instructions from the rep"
                        }
                    },
                    required=["notes"],
                    handler=self._record_additional_notes_handler
                ),
                FlowsFunctionSchema(
                    name="end_call",
                    description="End the conversation after saying goodbye.",
                    properties={},
                    required=[],
                    handler=self._end_call_handler
                )
            ],
            respond_immediately=True
        )

    def create_staff_confirmation_node(self) -> NodeConfig:
        return NodeConfig(
            name="staff_confirmation",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": """You just asked if they'd like to speak with your manager.

- If yes/sure/please/okay → call dial_staff
- If no/nevermind/continue → call decline_transfer"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="dial_staff",
                    description="Transfer to manager when they confirm.",
                    properties={},
                    required=[],
                    handler=self._dial_staff_handler
                ),
                FlowsFunctionSchema(
                    name="decline_transfer",
                    description="Continue to call wrap-up if they decline transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_verification_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "tts_say",
                "text": "Would you like to speak with my manager?"
            }]
        )

    def create_transfer_initiated_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_initiated",
            task_messages=[],
            functions=[],
            pre_actions=[{
                "type": "tts_say",
                "text": "Transferring you now, please hold."
            }],
            post_actions=[{
                "type": "end_conversation"
            }]
        )

    def create_transfer_failed_node(self) -> NodeConfig:
        return NodeConfig(
            name="transfer_failed",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": "The transfer failed. Apologize and wrap up the call."
            }],
            functions=[
                FlowsFunctionSchema(
                    name="wrap_up_call",
                    description="Proceed to call wrap-up after failed transfer.",
                    properties={},
                    required=[],
                    handler=self._return_to_verification_handler
                )
            ],
            respond_immediately=False,
            pre_actions=[{
                "type": "tts_say",
                "text": "I apologize, the transfer didn't go through."
            }]
        )

    # Field mappings: arg name -> state key (if different)
    VOLUNTEERED_FIELD_MAPPINGS = {
        # Plan info
        "network_status": "network_status",
        "plan_type": "plan_type",
        "plan_effective_date": "plan_effective_date",
        "plan_term_date": "plan_term_date",
        # CPT coverage
        "cpt_covered": "cpt_covered",
        "copay_amount": "copay_amount",
        "coinsurance_percent": "coinsurance_percent",
        "deductible_applies": "deductible_applies",
        "prior_auth_required": "prior_auth_required",
        "telehealth_covered": "telehealth_covered",
        # Accumulators
        "deductible_individual": "deductible_individual",
        "deductible_individual_met": "deductible_individual_met",
        "deductible_family": "deductible_family",
        "deductible_family_met": "deductible_family_met",
        "oop_max_individual": "oop_max_individual",
        "oop_max_individual_met": "oop_max_individual_met",
        "oop_max_family": "oop_max_family",
        "oop_max_family_met": "oop_max_family_met",
        "reference_number": "reference_number",
    }

    def _store_volunteered_info(self, args: Dict[str, Any], flow_manager: FlowManager) -> list[str]:
        """Store any volunteered info in state. Returns list of captured fields."""
        captured = []

        for arg_name, state_key in self.VOLUNTEERED_FIELD_MAPPINGS.items():
            value = args.get(arg_name, "")
            if isinstance(value, str):
                value = value.strip()
            # Store if non-empty and not "unknown" or "Unknown"
            if value and value.lower() not in ["unknown", ""]:
                flow_manager.state[state_key] = value
                captured.append(state_key)

        return captured

    async def _proceed_to_plan_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: greeting → plan_info (captured: {captured if captured else 'none'})")
        return None, self.create_plan_info_node()

    async def _record_network_status_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record network participation status to state and database."""
        try:
            status = args["status"]
            patient_id = flow_manager.state.get("patient_id")
            flow_manager.state["network_status"] = status

            if patient_id:
                db = get_async_patient_db()
                await db.update_field(patient_id, "network_status", status, self.organization_id)

            logger.info(f"Flow: Recorded network_status = {status}")
            return None, None
        except Exception as e:
            logger.error(f"Failed to record network status: {e}")
            return f"Error recording network status: {str(e)}", None

    async def _record_plan_type_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record plan type to state and database."""
        try:
            plan_type = args["plan_type"]
            patient_id = flow_manager.state.get("patient_id")
            flow_manager.state["plan_type"] = plan_type

            if patient_id:
                db = get_async_patient_db()
                await db.update_field(patient_id, "plan_type", plan_type, self.organization_id)

            logger.info(f"Flow: Recorded plan_type = {plan_type}")
            return None, None
        except Exception as e:
            logger.error(f"Failed to record plan type: {e}")
            return f"Error recording plan type: {str(e)}", None

    async def _record_plan_effective_date_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record plan effective date to state and database."""
        try:
            date = args["date"]
            patient_id = flow_manager.state.get("patient_id")
            flow_manager.state["plan_effective_date"] = date

            if patient_id:
                db = get_async_patient_db()
                await db.update_field(patient_id, "plan_effective_date", date, self.organization_id)

            logger.info(f"Flow: Recorded plan_effective_date = {date}")
            return None, None
        except Exception as e:
            logger.error(f"Failed to record plan effective date: {e}")
            return f"Error recording effective date: {str(e)}", None

    async def _record_plan_term_date_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record plan termination date to state and database."""
        try:
            date = args["date"]
            patient_id = flow_manager.state.get("patient_id")
            flow_manager.state["plan_term_date"] = date

            if patient_id:
                db = get_async_patient_db()
                await db.update_field(patient_id, "plan_term_date", date, self.organization_id)

            logger.info(f"Flow: Recorded plan_term_date = {date}")
            return None, None
        except Exception as e:
            logger.error(f"Failed to record plan term date: {e}")
            return f"Error recording term date: {str(e)}", None

    # ═══════════════════════════════════════════════════════════════════
    # CPT COVERAGE HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    async def _record_cpt_covered_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record whether CPT code is covered."""
        covered = args.get("covered", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["cpt_covered"] = covered

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "cpt_covered", covered, self.organization_id)

        logger.info(f"Flow: Recorded cpt_covered = {covered}")
        return None, None

    async def _record_copay_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record copay amount."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["copay_amount"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "copay_amount", amount, self.organization_id)

        logger.info(f"Flow: Recorded copay_amount = {amount}")
        return None, None

    async def _record_coinsurance_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record coinsurance percentage."""
        percent = args.get("percent", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["coinsurance_percent"] = percent

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "coinsurance_percent", percent, self.organization_id)

        logger.info(f"Flow: Recorded coinsurance_percent = {percent}")
        return None, None

    async def _record_deductible_applies_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record whether deductible applies to this service."""
        applies = args.get("applies", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["deductible_applies"] = applies

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "deductible_applies", applies, self.organization_id)

        logger.info(f"Flow: Recorded deductible_applies = {applies}")
        return None, None

    async def _record_prior_auth_required_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record whether prior authorization is required."""
        required = args.get("required", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["prior_auth_required"] = required

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "prior_auth_required", required, self.organization_id)

        logger.info(f"Flow: Recorded prior_auth_required = {required}")
        return None, None

    async def _record_telehealth_covered_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record whether telehealth is covered."""
        covered = args.get("covered", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["telehealth_covered"] = covered

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "telehealth_covered", covered, self.organization_id)

        logger.info(f"Flow: Recorded telehealth_covered = {covered}")
        return None, None

    # ═══════════════════════════════════════════════════════════════════
    # ACCUMULATOR HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    async def _record_deductible_individual_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record individual deductible amount."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["deductible_individual"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "deductible_individual", amount, self.organization_id)

        logger.info(f"Flow: Recorded deductible_individual = {amount}")
        return None, None

    async def _record_deductible_individual_met_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record how much of individual deductible has been met."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["deductible_individual_met"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "deductible_individual_met", amount, self.organization_id)

        logger.info(f"Flow: Recorded deductible_individual_met = {amount}")
        return None, None

    async def _record_deductible_family_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record family deductible amount."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["deductible_family"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "deductible_family", amount, self.organization_id)

        logger.info(f"Flow: Recorded deductible_family = {amount}")
        return None, None

    async def _record_deductible_family_met_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record how much of family deductible has been met."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["deductible_family_met"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "deductible_family_met", amount, self.organization_id)

        logger.info(f"Flow: Recorded deductible_family_met = {amount}")
        return None, None

    async def _record_oop_max_individual_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record individual out-of-pocket maximum."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["oop_max_individual"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "oop_max_individual", amount, self.organization_id)

        logger.info(f"Flow: Recorded oop_max_individual = {amount}")
        return None, None

    async def _record_oop_max_individual_met_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record how much of individual OOP max has been met."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["oop_max_individual_met"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "oop_max_individual_met", amount, self.organization_id)

        logger.info(f"Flow: Recorded oop_max_individual_met = {amount}")
        return None, None

    async def _record_oop_max_family_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record family out-of-pocket maximum."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["oop_max_family"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "oop_max_family", amount, self.organization_id)

        logger.info(f"Flow: Recorded oop_max_family = {amount}")
        return None, None

    async def _record_oop_max_family_met_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record how much of family OOP max has been met."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["oop_max_family_met"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "oop_max_family_met", amount, self.organization_id)

        logger.info(f"Flow: Recorded oop_max_family_met = {amount}")
        return None, None

    async def _record_allowed_amount_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record allowed amount if rep mentions it."""
        amount = args.get("amount", "Unknown")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["allowed_amount"] = amount

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "allowed_amount", amount, self.organization_id)

        logger.info(f"Flow: Recorded allowed_amount = {amount}")
        return None, None

    async def _record_reference_number_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record call reference number."""
        reference_number = args.get("reference_number", "")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["reference_number"] = reference_number

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "reference_number", reference_number, self.organization_id)

        logger.info(f"Flow: Recorded reference_number = {reference_number}")
        return None, None

    async def _record_rep_name_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record representative's name."""
        name = args.get("name", "")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["rep_name"] = name

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "rep_name", name, self.organization_id)

        logger.info(f"Flow: Recorded rep_name = {name}")
        return None, None

    async def _record_correction_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record correction to any previously captured field."""
        field_name = args.get("field_name", "")
        corrected_value = args.get("corrected_value", "")
        patient_id = flow_manager.state.get("patient_id")

        # Validate field name against allowed fields
        allowed_fields = {
            "copay_amount", "coinsurance_percent", "deductible_applies",
            "prior_auth_required", "telehealth_covered", "network_status",
            "plan_type", "deductible_individual", "deductible_individual_met",
            "deductible_family", "deductible_family_met", "oop_max_individual",
            "oop_max_individual_met", "oop_max_family", "oop_max_family_met"
        }

        if field_name not in allowed_fields:
            logger.warning(f"Flow: Attempted to correct invalid field: {field_name}")
            return None, None

        flow_manager.state[field_name] = corrected_value

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, field_name, corrected_value, self.organization_id)

        logger.info(f"Flow: CORRECTION recorded - {field_name} = {corrected_value}")
        return None, None

    async def _record_additional_notes_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[str, None]:
        """Record additional notes or special instructions from the rep."""
        notes = args.get("notes", "")
        patient_id = flow_manager.state.get("patient_id")
        flow_manager.state["additional_notes"] = notes

        if patient_id:
            db = get_async_patient_db()
            await db.update_field(patient_id, "additional_notes", notes, self.organization_id)

        logger.info(f"Flow: Recorded additional_notes = {notes}")
        return None, None

    async def _proceed_to_closing_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        """Transition from accumulators to closing node."""
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: accumulators → closing (captured: {captured if captured else 'none'})")
        return None, self.create_closing_node()

    async def _proceed_to_accumulators_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        """Transition from cpt_coverage to accumulators node."""
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: cpt_coverage → accumulators (captured: {captured if captured else 'none'})")
        return None, self.create_accumulators_node()

    async def _proceed_to_cpt_coverage_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        """Transition from plan_info to cpt_coverage node."""
        captured = self._store_volunteered_info(args, flow_manager)
        logger.info(f"Flow: plan_info → cpt_coverage (captured: {captured if captured else 'none'})")
        return None, self.create_cpt_coverage_node()

    async def _request_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.info("Flow: verification → staff_confirmation")
        return None, self.create_staff_confirmation_node()

    async def _dial_staff_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        staff_number = self.cold_transfer_config.get("staff_number")

        if not staff_number:
            logger.warning("Cold transfer requested but no staff_number configured")
            return None, self.create_transfer_failed_node()

        try:
            logger.info(f"Cold transfer initiated to: {staff_number}")

            if self.pipeline:
                self.pipeline.transfer_in_progress = True

            if self.transport:
                await self.transport.sip_call_transfer({"toEndPoint": staff_number})
                logger.info(f"SIP call transfer initiated: {staff_number}")

            return None, self.create_transfer_initiated_node()

        except Exception as e:
            logger.exception("Cold transfer failed")

            if self.pipeline:
                self.pipeline.transfer_in_progress = False

            return None, self.create_transfer_failed_node()

    async def _return_to_verification_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.info("Flow: returning to closing (transfer declined/failed)")
        return None, self.create_closing_node()

    async def _end_call_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, None]:
        from pipecat.frames.frames import EndTaskFrame
        from pipecat.processors.frame_processor import FrameDirection

        logger.info("Call ended by flow")

        try:
            if self.pipeline:
                await save_transcript_to_db(self.pipeline)
                logger.info("Transcript saved")

            patient_id = flow_manager.state.get("patient_id")
            if patient_id:
                db = get_async_patient_db()
                await db.update_call_status(patient_id, "Completed", self.organization_id)
                logger.info("Database: call_status = Completed")

            if self.context_aggregator:
                await self.context_aggregator.assistant().push_frame(
                    EndTaskFrame(), FrameDirection.UPSTREAM
                )

        except Exception as e:
            logger.error(f"Error in end_call_handler: {e}")

        return None, None
