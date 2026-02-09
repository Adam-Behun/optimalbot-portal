import json
from typing import Any, Dict

import openai
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat_flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)

from clients.demo_clinic_alpha.dialout_base_flow import DialoutBaseFlow

# ═══════════════════════════════════════════════════════════════════
# SHARED NORMALIZATION RULES (used by both observer and conv LLM)
# ═══════════════════════════════════════════════════════════════════

NORMALIZATION_RULES = """# Data Normalization (PLAIN NUMBERS ONLY)
- Currency: "five hundred" -> "500.00", NO $ symbol
- Percentages: "twenty percent" -> "20", NO % symbol
- Dates: MM/DD/YYYY
- Yes/No: "required"/"applies" -> "Yes", "not required" -> "No"
- Network: "in network"/"participating" -> "In-Network", "non-par" -> "Out-of-Network"
- Plan type: PPO, HMO, POS, EPO, HDHP
- "fully met" -> use the deductible/OOP amount as met value
- "N/A"/"not applicable" -> "N/A", "None"/"no copay" -> "None"
- Term date: "no termination date" -> "None"
# Individual vs Family
- "individual"/"member"/"single" -> deductible_individual or oop_max_individual
- "family"/"household" -> deductible_family or oop_max_family
- NEVER record individual amounts to family fields or vice versa"""


# Mapping from observer function arg names to flow_manager.state keys.
# Single source of truth for all extraction field names.
OBSERVER_FIELD_MAP = {
    "rep_first_name": "insurance_rep_first_name",
    "rep_last_initial": "insurance_rep_last_initial",
    "network_status": "network_status",
    "plan_type": "plan_type",
    "plan_effective_date": "plan_effective_date",
    "plan_term_date": "plan_term_date",
    "cpt_covered": "cpt_covered",
    "copay_amount": "copay_amount",
    "coinsurance_percent": "coinsurance_percent",
    "deductible_applies": "deductible_applies",
    "prior_auth_required": "prior_auth_required",
    "telehealth_covered": "telehealth_covered",
    "deductible_individual": "deductible_individual",
    "deductible_individual_met": "deductible_individual_met",
    "deductible_family": "deductible_family",
    "deductible_family_met": "deductible_family_met",
    "oop_max_individual": "oop_max_individual",
    "oop_max_individual_met": "oop_max_individual_met",
    "oop_max_family": "oop_max_family",
    "oop_max_family_met": "oop_max_family_met",
    "reference_number": "reference_number",
    "allowed_amount": "allowed_amount",
    "additional_notes": "additional_notes",
}

# State field names for extraction (derived from the map)
EXTRACTION_FIELDS = list(OBSERVER_FIELD_MAP.values())


class EligibilityVerificationFlow(DialoutBaseFlow):
    """Eligibility verification flow with silent observer for data extraction."""

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
1. Personal name given (not bot name like "Eva") -> CONVERSATION
2. "leave a message" or voicemail request -> VOICEMAIL
3. Contains "please hold", "hold please", press/dial/enter -> IVR
4. 1-3 words without "hold" (e.g., "Provider services.", "Speaking.", "Yes") -> CONVERSATION
5. Default for longer automated-sounding messages -> IVR

Output exactly one classification word: CONVERSATION, IVR, or VOICEMAIL."""

    IVR_NAVIGATION_GOAL = """Navigate to speak with a representative who can verify eligibility and benefits.

CALLER INFORMATION (provide exactly as shown when asked):
- Caller Name: {provider_agent_first_name}
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

    VOICEMAIL_MESSAGE_TEMPLATE = """Hi, this is {provider_agent_first_name}, calling from {facility_name} regarding eligibility verification for a patient. Please call us back at your earliest convenience. Thank you."""

    def __init__(self, call_data: Dict[str, Any], session_id: str = None, flow_manager: FlowManager = None,
                 main_llm=None, classifier_llm=None, context_aggregator=None, transport=None, pipeline=None,
                 organization_id: str = None, cold_transfer_config: Dict[str, Any] = None):
        super().__init__(
            call_data=call_data,
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
        """Return triage configuration for this flow."""
        return {
            "classifier_prompt": self.TRIAGE_CLASSIFIER_PROMPT,
            "ivr_navigation_goal": self.IVR_NAVIGATION_GOAL.format(
                provider_agent_first_name=self.call_data.get("provider_agent_first_name", ""),
                facility_name=self.call_data.get("facility_name", ""),
                tax_id=self.call_data.get("tax_id", ""),
                provider_name=self.call_data.get("provider_name", ""),
                provider_npi=self.call_data.get("provider_npi", ""),
                provider_call_back_phone=self.call_data.get("provider_call_back_phone", ""),
                insurance_member_id=self.call_data.get("insurance_member_id", ""),
                patient_name=self.call_data.get("patient_name", ""),
                date_of_birth=self.call_data.get("date_of_birth", ""),
            ),
            "voicemail_message": self.VOICEMAIL_MESSAGE_TEMPLATE.format(
                provider_agent_first_name=self.call_data.get("provider_agent_first_name", "a representative"),
                facility_name=self.call_data.get("facility_name", "our facility"),
            ),
        }

    def _init_domain_state(self):
        """Initialize eligibility verification specific state fields."""
        self.flow_manager.state["insurance_member_id"] = self.call_data.get("insurance_member_id", "")
        self.flow_manager.state["insurance_company_name"] = self.call_data.get("insurance_company_name", "")
        self.flow_manager.state["insurance_phone"] = self.call_data.get("insurance_phone", "")

        self.flow_manager.state["provider_agent_first_name"] = self.call_data.get("provider_agent_first_name", "")
        self.flow_manager.state["provider_agent_last_initial"] = self.call_data.get("provider_agent_last_initial", "")
        self.flow_manager.state["facility_name"] = self.call_data.get("facility_name", "")
        self.flow_manager.state["tax_id"] = self.call_data.get("tax_id", "")
        self.flow_manager.state["provider_name"] = self.call_data.get("provider_name", "")
        self.flow_manager.state["provider_npi"] = self.call_data.get("provider_npi", "")
        self.flow_manager.state["provider_call_back_phone"] = self.call_data.get("provider_call_back_phone", "")

        self.flow_manager.state["cpt_code"] = self.call_data.get("cpt_code", "")
        self.flow_manager.state["place_of_service"] = self.call_data.get("place_of_service", "")
        self.flow_manager.state["date_of_service"] = self.call_data.get("date_of_service", "")

    # ═══════════════════════════════════════════════════════════════════
    # GLOBAL INSTRUCTIONS (conv LLM persona)
    # ═══════════════════════════════════════════════════════════════════

    def _get_global_instructions(self) -> str:
        state = self.flow_manager.state
        facility = state.get("facility_name", "")
        provider_agent_first_name = state.get("provider_agent_first_name", "")
        provider_agent_last_initial = state.get("provider_agent_last_initial", "")

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
- Caller name: {provider_agent_first_name} {provider_agent_last_initial}.
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
- After saying goodbye, do not speak again - end the call immediately.

{NORMALIZATION_RULES}"""

    # ═══════════════════════════════════════════════════════════════════
    # OBSERVER METHODS
    # ═══════════════════════════════════════════════════════════════════

    def get_extraction_fields(self) -> list[str]:
        """Return extraction field names for observer context state injection."""
        return EXTRACTION_FIELDS

    def get_observer_system_prompt(self) -> str:
        return f"""You are a silent data extraction observer on an insurance eligibility verification call.
You listen to the full conversation and extract structured data from what the insurance representative says.

# Rules
- Extract ONLY information explicitly stated by the insurance representative
- ONLY extract reference_number if rep EXPLICITLY says "reference number is..." or "confirmation number..."
- NEVER use member ID as reference number
- NEVER guess or fabricate any values
- If no new data in this turn, call extract_eligibility_data with empty arguments
- Always overwrite with the latest value if the rep corrects something

{NORMALIZATION_RULES}"""

    def get_observer_tools(self) -> ToolsSchema:
        return ToolsSchema(standard_tools=[
            FunctionSchema(
                name="extract_eligibility_data",
                description="Extract eligibility data from the conversation. Only include fields explicitly mentioned by the rep. Empty args if nothing new.",
                properties={
                    "rep_first_name": {"type": "string", "description": "Rep's first name"},
                    "rep_last_initial": {"type": "string", "description": "Rep's last initial (single letter)"},
                    "network_status": {"type": "string", "enum": ["In-Network", "Out-of-Network", "Unknown"]},
                    "plan_type": {"type": "string", "description": "PPO, HMO, POS, EPO, HDHP"},
                    "plan_effective_date": {"type": "string", "description": "MM/DD/YYYY"},
                    "plan_term_date": {"type": "string", "description": "MM/DD/YYYY or None"},
                    "cpt_covered": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
                    "copay_amount": {"type": "string", "description": "Plain number: 50.00, None"},
                    "coinsurance_percent": {"type": "string", "description": "Plain number: 20, None"},
                    "deductible_applies": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
                    "prior_auth_required": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
                    "telehealth_covered": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
                    "deductible_individual": {"type": "string", "description": "Plain number: 500.00, N/A"},
                    "deductible_individual_met": {"type": "string", "description": "Plain number: 200.00"},
                    "deductible_family": {"type": "string", "description": "Plain number: 1000.00, N/A"},
                    "deductible_family_met": {"type": "string", "description": "Plain number: 500.00"},
                    "oop_max_individual": {"type": "string", "description": "Plain number: 3000.00, N/A"},
                    "oop_max_individual_met": {"type": "string", "description": "Plain number: 1500.00"},
                    "oop_max_family": {"type": "string", "description": "Plain number: 6000.00, N/A"},
                    "oop_max_family_met": {"type": "string", "description": "Plain number: 3000.00"},
                    "reference_number": {"type": "string", "description": "Exactly as stated by rep"},
                    "allowed_amount": {"type": "string", "description": "Plain number: 150.00, Unknown"},
                    "additional_notes": {"type": "string", "description": "Special instructions or limitations"},
                },
                required=[],
            )
        ])

    def register_observer_handlers(self, observer_llm, flow_manager):
        """Register the single extraction handler on the observer LLM."""
        flow = self

        async def _handle_extract(params):
            args = params.arguments
            patient_id = flow_manager.state.get("patient_id")
            extracted = []

            for arg_name, state_key in OBSERVER_FIELD_MAP.items():
                value = args.get(arg_name, "")
                if isinstance(value, str):
                    value = value.strip()
                if value:
                    flow_manager.state[state_key] = value
                    await flow._try_db_update(patient_id, "update_field", state_key, value)
                    extracted.append(f"{state_key}={value}")

            if extracted:
                logger.info(f"[Observer] Extracted: {', '.join(extracted)}")
            else:
                logger.debug("[Observer] No new data this turn")

            await params.result_callback(
                {"status": "ok"},
                properties=FunctionCallResultProperties(run_llm=False),
            )

        observer_llm.register_function("extract_eligibility_data", _handle_extract)

    async def run_final_extraction(self, transcript, flow_manager):
        """Run a final gpt-4o extraction sweep with full transcript before ending the call."""
        if not transcript:
            logger.debug("[Observer] No transcript for final extraction")
            return

        state = flow_manager.state

        # Find fields still missing
        missing = [f for f in EXTRACTION_FIELDS if not state.get(f)]
        if not missing:
            logger.info("[Observer] Final extraction: all fields already populated")
            return

        logger.info(f"[Observer] Final extraction: attempting to recover {len(missing)} missing fields: {missing}")

        # Build transcript text from the list of transcript entries
        if isinstance(transcript, list):
            transcript_text = "\n".join(
                f"{t.get('role', 'unknown')}: {t.get('content', '')}"
                for t in transcript if isinstance(t, dict)
            )
        else:
            transcript_text = str(transcript)

        current_state = {f: state.get(f) for f in EXTRACTION_FIELDS if state.get(f)}

        try:
            client = openai.AsyncOpenAI()

            tools = [{
                "type": "function",
                "function": {
                    "name": "extract_missing_data",
                    "description": "Extract any missing eligibility data from the full transcript.",
                    "parameters": {
                        "type": "object",
                        "properties": {f: {"type": "string"} for f in missing},
                        "required": [],
                    },
                },
            }]

            response = await client.chat.completions.create(
                model="gpt-4o",
                temperature=0,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": f"Extract missing eligibility data from this call transcript.\n\nAlready extracted:\n{json.dumps(current_state, indent=2)}\n\n{NORMALIZATION_RULES}\n\n- ONLY extract reference_number if rep EXPLICITLY said it\n- NEVER use member ID as reference number"},
                    {"role": "user", "content": transcript_text},
                ],
                tools=tools,
                tool_choice={"type": "function", "function": {"name": "extract_missing_data"}},
            )

            tool_calls = response.choices[0].message.tool_calls
            if tool_calls:
                args = json.loads(tool_calls[0].function.arguments)
                patient_id = state.get("patient_id")
                recovered = []
                for field, value in args.items():
                    if isinstance(value, str):
                        value = value.strip()
                    if value and field in missing:
                        state[field] = value
                        await self._try_db_update(patient_id, "update_field", field, value)
                        recovered.append(f"{field}={value}")

                if recovered:
                    logger.info(f"[Observer] Final extraction recovered: {', '.join(recovered)}")
                else:
                    logger.info("[Observer] Final extraction: no additional data found")

        except Exception as e:
            logger.error(f"[Observer] Final extraction failed: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # FLOW NODES (conv LLM — 6 pure flow-control functions)
    # ═══════════════════════════════════════════════════════════════════

    def create_greeting_node(self) -> NodeConfig:
        """Create greeting node for when a human answers."""
        return NodeConfig(
            name="greeting",
            role_messages=[{
                "role": "system",
                "content": self._get_global_instructions()
            }],
            task_messages=[{
                "role": "system",
                "content": """A human answered. Give your FULL greeting (with patient name) and collect the rep's name.

# Your First Response

STEP 1 - PARSE THEIR NAME (if stated):
- "This is Diana" -> first_name="Diana", last_initial=""
- "Marcus B. speaking" -> first_name="Marcus", last_initial="B"
- "Provider services" -> no name

STEP 2 - SAY YOUR FULL GREETING:
Your greeting MUST include the patient name. This step is important.

If they gave a name: "Hi [their name], this is [your name] calling from [facility] about eligibility verification for [patient name]. May I have your last initial for my records?"

If no name given: "Hi, this is [your name] calling from [facility] about eligibility verification for [patient name]. May I have your first name and last initial?"

CRITICAL: Even if the rep asks "What's your name?" or "What facility?" - you still give the FULL greeting above. Do NOT just answer their question. The patient name MUST be in your first response.

# Example
Rep: "Thank you for holding. This is Diana. May I have your name and the facility you are calling from?"
You say: "Hi Diana, this is Jennifer calling from Specialty Surgery Associates about eligibility verification for Robert Williams. May I have your last initial for my records?"

# After Rep Gives Last Initial
Say "Perfect, thank you." and WAIT. Do not ask questions - the rep will ask you for member info.

# Answering Rep's Questions (After Greeting)
- Member name -> "[patient name]"
- Date of birth -> "[date of birth]"
- Member ID -> "[member ID]"
- Tax ID -> "[tax ID]"

# When to Proceed
When rep indicates they can help OR gives any eligibility info -> call proceed_to_plan_info."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_plan_info",
                    description="Move to plan info when rep is ready to help or gives eligibility info.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_plan_info_handler
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            context_strategy=ContextStrategyConfig(
                strategy=ContextStrategy.RESET
            )
        )

    def create_plan_info_node(self) -> NodeConfig:
        """Gather basic plan information."""
        state = self.flow_manager.state
        facility = state.get("facility_name", "")

        has_network = bool(state.get("network_status"))
        has_plan_type = bool(state.get("plan_type"))
        has_effective = bool(state.get("plan_effective_date"))
        has_term = bool(state.get("plan_term_date"))

        missing = []
        if not has_network:
            missing.append(f'Network status: "Is {facility} participating in the network?"')
        if not has_plan_type:
            missing.append('Plan type: "What type of plan is this?"')
        if not has_effective:
            missing.append('Effective date: "What is the effective date?"')
        if not has_term:
            missing.append('Term date: "Is there a term date?"')

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

# Already Captured (by observer)
{captured_text}

# Still Need (Plan Info)
{missing_text}

# Instructions
- If all 4 plan info fields are captured, call proceed_to_cpt_coverage IMMEDIATELY
- Only ask about MISSING plan info fields - never re-ask what's already captured
- Before calling proceed_to_cpt_coverage, briefly restate the gathered values for confirmation. Example: "So I have you as in-network with a PPO plan, effective January first with no term date - is that correct?"
- If rep asks about CPT code or volunteers coverage info, call proceed_to_cpt_coverage

# CRITICAL: DO NOT END THE CALL
- When rep says "anything else?" - if plan info fields are missing, ask for them
- If all plan info is captured, call proceed_to_cpt_coverage - DO NOT say goodbye
- DO NOT say "that covers everything" or thank them until the ENTIRE call is done
- You still need CPT coverage details, accumulators, and reference number after this"""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_cpt_coverage",
                    description="Move to CPT coverage questions after plan info is gathered.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_cpt_coverage_handler
                )
            ],
            respond_immediately=True
        )

    def create_cpt_coverage_node(self) -> NodeConfig:
        """Gather CPT coverage details."""
        state = self.flow_manager.state
        cpt_code = state.get("cpt_code", "")
        date_of_service = state.get("date_of_service", "")
        place_of_service = state.get("place_of_service", "")

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

# Already Captured (by observer)
{captured_text}

# Still Need
{missing_text}

# CRITICAL: If CPT is NOT COVERED
If the rep says the CPT code is "not covered", "excluded", "not a covered benefit":
- Proceed directly to accumulators (you still need deductible/OOP info and reference number)

# Instructions
- If cpt_covered = "No", call proceed_to_accumulators IMMEDIATELY (skip other CPT questions)
- If all 6 CPT coverage fields are captured, briefly restate values for confirmation then call proceed_to_accumulators
- Example: "So I have coverage confirmed with a fifty dollar copay, twenty percent coinsurance, deductible applies, no prior auth needed, and telehealth covered - does that sound right?"
- Only ask about fields NOT yet mentioned by the rep
- NEVER re-ask for information the rep just provided. This step is important.

# When to Call proceed_to_accumulators
- When rep confirms CPT coverage info — call proceed_to_accumulators even if some fields weren't asked
- When rep says "anything else?" or "is there anything else?" — call proceed_to_accumulators IMMEDIATELY
- When rep volunteers deductible, OOP, or reference info — call proceed_to_accumulators IMMEDIATELY
- DO NOT ask deductible/OOP questions from this node — that is the next step
- DO NOT say goodbye or end the conversation from this node

# Handling Rep Questions
- Date of service: "{date_of_service or "Not yet determined. Is it okay to use today's date?"}"
- Place of service: "{place_of_service}" """
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_accumulators",
                    description="Move to accumulators after CPT coverage is gathered.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_accumulators_handler
                )
            ],
            respond_immediately=True
        )

    def create_accumulators_node(self) -> NodeConfig:
        """Gather deductible and OOP max information, then get a reference number."""
        state = self.flow_manager.state

        acc_fields = {
            "deductible_individual": ("Individual deductible", '"What is the individual deductible amount?"'),
            "deductible_individual_met": ("Individual deductible met", '"How much of the individual deductible has been met?"'),
            "deductible_family": ("Family deductible", '"What is the family deductible amount?"'),
            "deductible_family_met": ("Family deductible met", '"How much of the family deductible has been met?"'),
            "oop_max_individual": ("Individual OOP max", '"What is the individual out-of-pocket maximum?"'),
            "oop_max_individual_met": ("Individual OOP met", '"How much of the individual out-of-pocket maximum has been met?"'),
            "oop_max_family": ("Family OOP max", '"What is the family out-of-pocket maximum?"'),
            "oop_max_family_met": ("Family OOP met", '"How much of the family out-of-pocket maximum has been met?"'),
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
                "content": f"""# Context
You are MID-CALL with an insurance rep who has already verified your identity. Continue the conversation naturally - do NOT re-introduce yourself or say hello.

# Goal
Gather deductible and out-of-pocket maximum information, then get a reference number.

# Already Captured (by observer)
{captured_text}

# Still Need
{missing_text}

# CRITICAL: Individual vs Family Accumulators
Listen carefully to whether the rep says "individual" or "family":
- "individual" / "member" / "single" / "subscriber" -> individual fields
- "family" / "household" -> family fields

If rep ONLY provides individual OR family amounts, ask ONCE: "Do you have the [family/individual] amounts as well?"

# CRITICAL: When Rep Says Info Not Available
If rep says "I only have individual" or "I don't have family amounts":
- STOP asking about the unavailable type
- Do NOT repeatedly ask for info the rep said they don't have
- One "no" is enough - move on immediately

# Reference Number Rules - READ CAREFULLY
- You MUST ask for a reference number: "May I have a reference number for this call?"
- ONLY acknowledge a reference number AFTER the rep EXPLICITLY provides one
- NEVER use the member ID as a reference number - they are DIFFERENT things
- NEVER guess, invent, or fabricate a reference number
- If the rep says "anything else?" but you don't have a reference number yet, ask for one

**HALLUCINATION WARNING**: You MUST NOT invent reference numbers. If the rep has not explicitly said a reference number in their message, ASK for one.

# WHEN TO CALL proceed_to_closing
Before calling proceed_to_closing, briefly restate the accumulators and reference number for confirmation.
Example: "So I have the individual deductible at five hundred with four twelve met, family at one thousand with five hundred met, individual out-of-pocket max at three thousand with fifteen hundred met, and the reference number is A B C one two three - is that all correct?"

ONLY call proceed_to_closing when the rep has given you a reference number.
If the rep has NOT given a reference number, ask for one first."""
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_closing",
                    description="Move to closing after accumulators and reference number are gathered.",
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
- Do NOT say goodbye more than once"""
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
            respond_immediately=True
        )

    # ═══════════════════════════════════════════════════════════════════
    # TRANSITION HANDLERS (simplified — no data recording)
    # ═══════════════════════════════════════════════════════════════════

    async def _proceed_to_plan_info_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.debug("[Flow] Node: greeting -> plan_info")
        return None, self.create_plan_info_node()

    async def _proceed_to_cpt_coverage_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.debug("[Flow] Node: plan_info -> cpt_coverage")
        return None, self.create_cpt_coverage_node()

    async def _proceed_to_accumulators_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        logger.debug("[Flow] Node: cpt_coverage -> accumulators")
        return None, self.create_accumulators_node()

    async def _proceed_to_closing_handler(
        self, args: Dict[str, Any], flow_manager: FlowManager
    ) -> tuple[None, "NodeConfig"]:
        # Guard: Prevent duplicate transitions
        if flow_manager.current_node == "closing":
            logger.debug("[Flow] Already on closing node, ignoring duplicate proceed_to_closing call")
            return None, None

        # Guard: Only proceed if reference_number has been recorded (by observer)
        reference_number = flow_manager.state.get("reference_number")
        if not reference_number:
            logger.warning("[Flow] proceed_to_closing called but no reference_number recorded - staying on accumulators")
            return None, None

        logger.debug("[Flow] Node: accumulators -> closing")
        return None, self.create_closing_node()

    # ═══════════════════════════════════════════════════════════════════
    # END CALL HANDLER (with final extraction)
    # ═══════════════════════════════════════════════════════════════════

    async def _end_call_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, None]:
        """End the call with a final extraction sweep."""
        # Guard: Prevent multiple EndTaskFrame calls
        if flow_manager.state.get("_call_ended"):
            logger.debug("[Flow] end_call already called, ignoring duplicate")
            return None, None

        flow_manager.state["_call_ended"] = True

        # Run final extraction before ending
        if hasattr(self, 'pipeline') and self.pipeline:
            transcript = getattr(self.pipeline, 'transcripts', [])
            await self.run_final_extraction(transcript, flow_manager)

        # Call base class work directly (skips guard since we already set it)
        await self._end_call_work(flow_manager)
        return None, None

    # Transfer handlers inherited from DialoutBaseFlow:
    # - _request_staff_handler
    # - _dial_staff_handler
    # - _return_to_closing_handler
