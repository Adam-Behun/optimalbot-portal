from typing import Dict, Any
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema, ContextStrategy, ContextStrategyConfig
from loguru import logger

from clients.demo_clinic_alpha.dialout_base_flow import DialoutBaseFlow


class EligibilityVerificationFlow(DialoutBaseFlow):
    """Eligibility verification flow with triage support."""

    # ═══════════════════════════════════════════════════════════════════
    # TRIAGE CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════

    TRIAGE_CLASSIFIER_PROMPT = """You are a call classification system for OUTBOUND insurance verification calls.

HUMAN CONVERSATION (respond "CONVERSATION"):
- Personal greetings: "Hello?", "Hi", "Speaking", "This is [name]"
- Department greetings with personal name: "Insurance verification, this is Sarah"
- Representative with ID: "[Name] speaking, representative ID [number]"
- Interactive responses: "Who is this?", "How can I help you?"
- Natural speech with pauses and informal tone

IVR SYSTEM (respond "IVR"):
- Menu options: "Press 1 for claims", "Press 2 for eligibility"
- Automated instructions: "Please enter your provider NPI"
- System prompts: "Thank you for calling [insurance company]"
- Hold messages: "Please hold while we transfer you"
- After-hours with options: "dial extension now", "press 1 to..." (still IVR even if closed)
- Virtual assistants: "I'm your virtual assistant", "I'm the digital assistant"
- Conversational AI: Named bots like "I'm Eva", automated but natural-sounding
- AI mid-flow: "I see you're calling about...", "Let me look that up", "Let me transfer you"

VOICEMAIL SYSTEM (respond "VOICEMAIL"):
- Voicemail greetings: "leave a message", "please leave a message after the tone"
- Carrier messages: "The number you have dialed is not available"
- Mailbox messages: "This mailbox is full"

DECISION RULES (in order):
1. Personal name given (not bot name like "Eva") → CONVERSATION
2. "leave a message" or voicemail request → VOICEMAIL
3. Contains "please hold", "hold please", press/dial/enter → IVR
4. 1-3 words without "hold" (e.g., "Provider services.", "Speaking.", "Yes") → CONVERSATION
5. Default for longer automated-sounding messages → IVR

Output exactly one classification word: CONVERSATION, IVR, or VOICEMAIL."""

    IVR_NAVIGATION_GOAL = """Navigate to speak with a representative who can verify eligibility and benefits.

CALLER INFORMATION (provide exactly as shown when asked):
- Caller Name: {caller_name}
- Facility Name: {facility_name}
- Tax ID: {tax_id}
- Provider Name: {provider_name}
- Provider NPI: {provider_npi}
- Callback Phone: {provider_call_back_phone}

MEMBER INFORMATION (provide exactly as shown when asked):
- Member ID: {insurance_member_id}
- Patient Name: {patient_name}
- Date of Birth: {date_of_birth}

DATA ENTRY INSTRUCTIONS:
- DTMF for numeric values: <dtmf>1</dtmf><dtmf>2</dtmf>... (add <dtmf>#</dtmf> if requested)
- SPEAK for text/alphanumeric: Output as natural text for TTS
- Member ID: SPEAK if contains letters, DTMF if purely numeric
- Dates: MMDDYYYY format as DTMF
- Tax ID: Digits only, no dashes

NAVIGATION INSTRUCTIONS:
- When asked if you're a member or provider: Say "Health care professional"
- When asked why you're calling: Say "Benefits" or "Eligibility"
- When asked for benefit type: Say the type of service being verified
- When offered menu options: Choose "eligibility", "benefits", "provider services", or "speak to representative"
- When asked to confirm information: Say "Yes" or "Correct"
- When offered surveys: Say "No, thank you"
- When put on hold or told to wait: Output <ivr>wait</ivr> (wait silently)

Goal: Reach a human representative who can verify coverage details."""

    VOICEMAIL_MESSAGE_TEMPLATE = """Hi, this is {caller_name}, calling from {facility_name} regarding eligibility verification for a patient. Please call us back at your earliest convenience. Thank you."""

    def __init__(self, patient_data: Dict[str, Any], session_id: str = None, flow_manager: FlowManager = None,
                 main_llm=None, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None, cold_transfer_config: Dict[str, Any] = None):
        super().__init__(
            patient_data=patient_data,
            session_id=session_id,
            flow_manager=flow_manager,
            main_llm=main_llm,
            classifier_llm=classifier_llm,
            context_aggregator=context_aggregator,
            transport=transport,
            pipeline=pipeline,
            organization_id=organization_id,
            cold_transfer_config=cold_transfer_config,
        )

    def get_triage_config(self) -> dict:
        """Return triage configuration for this flow.

        Called by PipelineFactory to configure TriageDetector and IVRNavigationProcessor.
        Uses patient_data directly since flow_manager may not be set yet.
        """
        return {
            "classifier_prompt": self.TRIAGE_CLASSIFIER_PROMPT,
            "ivr_navigation_goal": self.IVR_NAVIGATION_GOAL.format(
                # Caller/Provider info
                caller_name=self._patient_data.get("caller_name", ""),
                facility_name=self._patient_data.get("facility_name", ""),
                tax_id=self._patient_data.get("tax_id", ""),
                provider_name=self._patient_data.get("provider_name", ""),
                provider_npi=self._patient_data.get("provider_npi", ""),
                provider_call_back_phone=self._patient_data.get("provider_call_back_phone", ""),
                # Member info
                insurance_member_id=self._patient_data.get("insurance_member_id", ""),
                patient_name=self._patient_data.get("patient_name", ""),
                date_of_birth=self._patient_data.get("date_of_birth", ""),
            ),
            "voicemail_message": self.VOICEMAIL_MESSAGE_TEMPLATE.format(
                caller_name=self._patient_data.get("caller_name", "a representative"),
                facility_name=self._patient_data.get("facility_name", "our facility"),
            ),
        }

    def _init_domain_state(self):
        """Initialize eligibility verification specific state fields."""
        # Insurance identification (3 fields - patient_id, patient_name, date_of_birth handled by base)
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
- Avoid repetitive acknowledgments - don't say "thank you" or "got it" after every response. Just proceed to your next question.
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

YOU ARE THE CALLER. The rep will ask YOU identification questions to verify who you are. Answer them:
- "What's your name?" → "[your name]"
- "Facility name?" / "Where are you calling from?" → "[facility name]"
- "Tax ID?" → "[tax ID from your provider info]"
- "Member name?" / "Patient name?" → "[patient name]"
- "Date of birth?" → "[date of birth]"
- "Member ID?" → "[member ID]"

DO NOT ask the rep to confirm anything. YOU answer THEIR questions. Wait for them to ask before providing info.

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
                        "copay_amount": {"type": "string", "description": "Plain number with 2 decimals: '50.00', 'None' if mentioned"},
                        "coinsurance_percent": {"type": "string", "description": "Plain number: '20', 'None' if mentioned"},
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
                ),
                self._request_staff_schema(),
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
                    description="Move to CPT coverage. Include ANY info the rep already volunteered. NORMALIZE TO PLAIN NUMBERS: copay '75.00' not '$75' or 'seventy-five', coinsurance '20' not '20%'.",
                    properties={
                        # CPT coverage
                        "cpt_covered": {"type": "string", "enum": ["Yes", "No", "Unknown"], "description": "If rep said covered/not covered"},
                        "copay_amount": {"type": "string", "description": "Plain number with 2 decimals: '50.00', 'None' if mentioned"},
                        "coinsurance_percent": {"type": "string", "description": "Plain number: '20', 'None' if mentioned"},
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

# Data Normalization (PLAIN NUMBERS ONLY - no $ or % symbols)
- Currency: "50.00", "None" | Percentages: "20", "None"
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
FORMAT: Plain number with 2 decimals (e.g., "50.00"), "None", or "Unknown". NO $ symbol.

EXAMPLES:
- "fifty dollars per service" → "50.00"
- "twenty five dollar copay" → "25.00"
- "seventy-five copay" → "75.00"
- "no copay" → "None"
- "copay does not apply" → "None" """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Copay amount as plain number (e.g., '50.00', 'None', 'Unknown')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_copay_handler
                ),
                FlowsFunctionSchema(
                    name="record_coinsurance",
                    description="""Record coinsurance percentage.

WHEN TO USE: After rep provides coinsurance information.
FORMAT: Plain number (e.g., "20"), "None", or "Unknown". NO % symbol.

EXAMPLES:
- "twenty percent coinsurance" → "20"
- "zero percent" → "0"
- "no coinsurance" → "None"
- "you pay 80 percent, insurance pays 20" → "80" (patient responsibility) """,
                    properties={
                        "percent": {
                            "type": "string",
                            "description": "Coinsurance percentage as plain number (e.g., '20', 'None', 'Unknown')"
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

If rep ONLY provides individual OR family amounts, ask ONCE: "Do you have the [family/individual] amounts as well?"

# CRITICAL: When Rep Says Info Not Available
If rep says "I only have individual" or "I don't have family amounts" or similar:
- STOP asking about the unavailable accumulator type
- Do NOT repeatedly ask for info the rep said they don't have
- Record what you have and proceed to reference number
- One "no" is enough - move on immediately

Example:
- Rep says "The individual deductible is $750" → record it, ask about family ONCE
- Rep says "I only have individual" → STOP asking about family, proceed to reference number

# Instructions
- Only ask about MISSING fields - never re-ask what's already captured
- When rep answers, record via the appropriate function

# CRITICAL: Reference Number and Closing
- If you have ALREADY recorded a reference number (in this turn or earlier), call proceed_to_closing
- If you do NOT have a reference number yet, ask: "May I have a reference number for this call?"
- NEVER ask for a reference number if you already recorded one - check your function calls first

# Reference Number Rules - READ CAREFULLY
- You MUST ask for a reference number: "May I have a reference number for this call?"
- ONLY record a reference number AFTER the rep EXPLICITLY provides one IN THEIR MESSAGE
- The reference number MUST appear in the rep's actual words - look for phrases like "reference number is..." or "confirmation number..."
- NEVER use the member ID as a reference number - they are DIFFERENT things
- NEVER guess, invent, or fabricate a reference number
- If the rep says "anything else?" but you don't have a reference number yet, ask for one

**HALLUCINATION WARNING**: You MUST NOT invent reference numbers. If the rep has not explicitly said a reference number in their message, you CANNOT record one. Check the rep's ACTUAL words - did they SAY a reference number? If not, ASK for one.

WRONG: Recording member ID "CIG334455667" as reference number
WRONG: Making up "CIG-2025-1234" when rep never said it
RIGHT: Ask "May I have a reference number?" and wait for rep to EXPLICITLY say one

# WHEN TO CALL proceed_to_closing
ONLY call proceed_to_closing when BOTH conditions are met:
1. You have recorded a reference number that the rep ACTUALLY SAID
2. You have captured all required accumulator info

If the rep has NOT given you a reference number, you MUST ask for one before proceeding.

# CRITICAL: Handling Corrections
If the rep CORRECTS a value they gave earlier:
1. Your spoken response MUST SAY: "I'll update that, thank you for the correction."
2. Call record_correction function
3. Then continue
DO NOT just say "Thank you, goodbye" - you must acknowledge the correction in your words.

# Data Normalization (PLAIN NUMBERS ONLY - no $ symbols)
- Amounts: "five hundred" → "500.00", "six thousand" → "6000.00"
- Cents: "eleven seventy point seven four" → "1170.74"
- "fully met" → deductible_family_met = deductible_family amount"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="record_deductible_individual",
                    description="""Record INDIVIDUAL deductible amount. Use ONLY for individual/member/single/subscriber deductibles.

WHEN TO USE: Rep explicitly says "individual", "member", "single", or "subscriber" deductible.
DO NOT USE: When rep says "family" or "household" - use record_deductible_family instead.
FORMAT: Plain number with 2 decimals (e.g., "500.00") or "N/A". NO $ symbol.

EXAMPLES:
- "individual deductible is two hundred fifty" → "250.00" ✓
- "the member deductible is five hundred" → "500.00" ✓
- "family deductible is one thousand" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Individual deductible amount as plain number (e.g., '500.00', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_deductible_individual_handler
                ),
                FlowsFunctionSchema(
                    name="record_deductible_individual_met",
                    description="""Record how much of individual deductible has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Plain number with 2 decimals (e.g., "200.00"). NO $ symbol.

EXAMPLES:
- "two hundred of the deductible met" → "200.00"
- "fully met" → Use the deductible amount
- "nothing met yet" → "0.00" """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of individual deductible met as plain number (e.g., '200.00')"
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
FORMAT: Plain number with 2 decimals (e.g., "500.00") or "N/A". NO $ symbol.

EXAMPLES:
- "family deductible is five hundred" → "500.00" ✓
- "household deductible is one thousand" → "1000.00" ✓
- "individual deductible is two fifty" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Family deductible amount as plain number (e.g., '500.00', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_deductible_family_handler
                ),
                FlowsFunctionSchema(
                    name="record_deductible_family_met",
                    description="""Record how much of family deductible has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Plain number with 2 decimals (e.g., "500.00"). NO $ symbol.

EXAMPLES:
- "fully met" or "satisfied" → Use the deductible amount
- "five hundred met" → "500.00"
- "nothing applied yet" → "0.00" """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of family deductible met as plain number (e.g., '500.00')"
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
FORMAT: Plain number with 2 decimals (e.g., "3000.00") or "N/A". NO $ symbol.

EXAMPLES:
- "individual out of pocket max is three thousand" → "3000.00" ✓
- "family OOP max is six thousand" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Individual OOP maximum as plain number (e.g., '3000.00', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_oop_max_individual_handler
                ),
                FlowsFunctionSchema(
                    name="record_oop_max_individual_met",
                    description="""Record how much of individual OOP max has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Plain number with 2 decimals (e.g., "1500.00"). NO $ symbol.""",
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of individual OOP max met as plain number (e.g., '1500.00')"
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
FORMAT: Plain number with 2 decimals (e.g., "6000.00") or "N/A". NO $ symbol.

EXAMPLES:
- "family out of pocket max is six thousand" → "6000.00" ✓
- "household OOP max is eight thousand" → "8000.00" ✓
- "individual OOP is three thousand" → DO NOT USE THIS FUNCTION """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Family OOP maximum as plain number (e.g., '6000.00', 'N/A')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_oop_max_family_handler
                ),
                FlowsFunctionSchema(
                    name="record_oop_max_family_met",
                    description="""Record how much of family OOP max has been met.

WHEN TO USE: After rep provides amount met.
FORMAT: Plain number with 2 decimals (e.g., "1170.74"). NO $ symbol.

EXAMPLES:
- "one thousand one hundred seventy dollars and seventy four cents" → "1170.74"
- "about twelve hundred" → Ask for exact amount, then record """,
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Amount of family OOP max met as plain number (e.g., '1170.74')"
                        }
                    },
                    required=["amount"],
                    handler=self._record_oop_max_family_met_handler
                ),
                FlowsFunctionSchema(
                    name="record_allowed_amount",
                    description="""Record allowed amount if rep mentions it.

WHEN TO USE: If rep provides an allowed/approved amount for the service.
FORMAT: Plain number with 2 decimals (e.g., "150.00") or "Unknown". NO $ symbol.""",
                    properties={
                        "amount": {
                            "type": "string",
                            "description": "Allowed amount as plain number (e.g., '150.00', 'Unknown')"
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
                            "description": "The CORRECTED value as plain number (e.g., '50.00' for currency, '20' for percent)"
                        }
                    },
                    required=["field_name", "corrected_value"],
                    handler=self._record_correction_handler
                ),
                FlowsFunctionSchema(
                    name="proceed_to_closing",
                    description="""Move to closing node to end the call.

ONLY call this AFTER you have recorded a reference number via record_reference_number.
If you have not yet recorded a reference number, ASK for one first - do NOT call this function.""",
                    properties={},
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

    # Transfer nodes are inherited from DialoutBaseFlow:
    # - create_staff_confirmation_node
    # - create_transfer_initiated_node
    # - create_transfer_pending_node
    # - create_transfer_failed_node

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
        # NOTE: reference_number intentionally excluded - must use record_reference_number function
    }

    async def _store_volunteered_info(self, args: Dict[str, Any], flow_manager: FlowManager) -> list[str]:
        """Store any volunteered info in state AND persist to DB. Returns list of captured fields."""
        captured = []
        patient_id = flow_manager.state.get("patient_id")

        for field in self.VOLUNTEERED_FIELD_MAPPINGS.keys():
            value = args.get(field, "")
            if isinstance(value, str):
                value = value.strip()
            if value and value.lower() not in ["unknown", ""]:
                flow_manager.state[field] = value
                await self._try_db_update(patient_id, "update_field", field, value)
                captured.append(field)

        return captured

    async def _proceed_to_plan_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        captured = await self._store_volunteered_info(args, flow_manager)
        logger.debug(f"[Flow] Node: greeting → plan_info (captured: {captured if captured else 'none'})")
        return None, self.create_plan_info_node()

    async def _record_network_status_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("network_status", args.get("status", "Unknown"), flow_manager)

    async def _record_plan_type_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("plan_type", args.get("plan_type", "Unknown"), flow_manager)

    async def _record_plan_effective_date_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("plan_effective_date", args.get("date", "Unknown"), flow_manager)

    async def _record_plan_term_date_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("plan_term_date", args.get("date", "Unknown"), flow_manager)

    # ═══════════════════════════════════════════════════════════════════
    # CPT COVERAGE HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    async def _record_cpt_covered_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("cpt_covered", args.get("covered", "Unknown"), flow_manager)

    async def _record_copay_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("copay_amount", args.get("amount", "Unknown"), flow_manager)

    async def _record_coinsurance_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("coinsurance_percent", args.get("percent", "Unknown"), flow_manager)

    async def _record_deductible_applies_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("deductible_applies", args.get("applies", "Unknown"), flow_manager)

    async def _record_prior_auth_required_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("prior_auth_required", args.get("required", "Unknown"), flow_manager)

    async def _record_telehealth_covered_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("telehealth_covered", args.get("covered", "Unknown"), flow_manager)

    # ═══════════════════════════════════════════════════════════════════
    # ACCUMULATOR HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    async def _record_deductible_individual_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("deductible_individual", args.get("amount", "Unknown"), flow_manager)

    async def _record_deductible_individual_met_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("deductible_individual_met", args.get("amount", "Unknown"), flow_manager)

    async def _record_deductible_family_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("deductible_family", args.get("amount", "Unknown"), flow_manager)

    async def _record_deductible_family_met_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("deductible_family_met", args.get("amount", "Unknown"), flow_manager)

    async def _record_oop_max_individual_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("oop_max_individual", args.get("amount", "Unknown"), flow_manager)

    async def _record_oop_max_individual_met_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("oop_max_individual_met", args.get("amount", "Unknown"), flow_manager)

    async def _record_oop_max_family_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("oop_max_family", args.get("amount", "Unknown"), flow_manager)

    async def _record_oop_max_family_met_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("oop_max_family_met", args.get("amount", "Unknown"), flow_manager)

    async def _record_allowed_amount_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("allowed_amount", args.get("amount", "Unknown"), flow_manager)

    async def _record_reference_number_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("reference_number", args.get("reference_number", ""), flow_manager)

    async def _record_rep_name_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("rep_name", args.get("name", ""), flow_manager)

    CORRECTABLE_FIELDS = {
        "copay_amount", "coinsurance_percent", "deductible_applies",
        "prior_auth_required", "telehealth_covered", "network_status",
        "plan_type", "deductible_individual", "deductible_individual_met",
        "deductible_family", "deductible_family_met", "oop_max_individual",
        "oop_max_individual_met", "oop_max_family", "oop_max_family_met"
    }

    async def _record_correction_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        """Record correction to any previously captured field."""
        field_name = args.get("field_name", "")
        if field_name not in self.CORRECTABLE_FIELDS:
            logger.warning(f"[Flow] Attempted to correct invalid field: {field_name}")
            return None, None
        logger.info(f"[Flow] Correction: {field_name}")
        return await self._record_field(field_name, args.get("corrected_value", ""), flow_manager)

    async def _record_additional_notes_handler(self, args: Dict[str, Any], flow_manager: FlowManager):
        return await self._record_field("additional_notes", args.get("notes", ""), flow_manager)

    async def _proceed_to_closing_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        """Transition from accumulators to closing node."""
        # Guard: Only proceed if reference_number has been recorded
        reference_number = flow_manager.state.get("reference_number")
        if not reference_number:
            logger.warning("[Flow] proceed_to_closing called but no reference_number recorded - staying on accumulators")
            return None, None

        captured = await self._store_volunteered_info(args, flow_manager)
        logger.debug(f"[Flow] Node: accumulators → closing (captured: {captured if captured else 'none'})")
        return None, self.create_closing_node()

    async def _proceed_to_accumulators_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        """Transition from cpt_coverage to accumulators node."""
        captured = await self._store_volunteered_info(args, flow_manager)
        logger.debug(f"[Flow] Node: cpt_coverage → accumulators (captured: {captured if captured else 'none'})")
        return None, self.create_accumulators_node()

    async def _proceed_to_cpt_coverage_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        """Transition from plan_info to cpt_coverage node."""
        captured = await self._store_volunteered_info(args, flow_manager)
        logger.debug(f"[Flow] Node: plan_info → cpt_coverage (captured: {captured if captured else 'none'})")
        return None, self.create_cpt_coverage_node()

    # Transfer handlers are inherited from DialoutBaseFlow:
    # - _request_staff_handler
    # - _dial_staff_handler
    # - _return_to_closing_handler
    # - _end_call_handler
