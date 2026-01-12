import os
from typing import Dict, Any

from openai import AsyncOpenAI
from pipecat_flows import FlowManager, NodeConfig, FlowsFunctionSchema
from loguru import logger

from backend.models.patient import get_async_patient_db
from clients.demo_clinic_alpha.dialin_base_flow import DialinBaseFlow
from .schema import MEDICATIONS, PRESCRIPTION_STATUS


class _MockFlowManager:
    def __init__(self):
        self.state = {}


async def warmup_openai(call_data: dict = None):
    try:
        call_data = call_data or {"organization_name": "Demo Clinic Alpha"}
        flow = PrescriptionStatusFlow(
            call_data=call_data,
            session_id="warmup",
            flow_manager=_MockFlowManager(),
            main_llm=None,
        )
        greeting_node = flow.create_greeting_node()
        messages = []
        for msg in greeting_node.get("role_messages") or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        for msg in greeting_node.get("task_messages") or []:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": "Hi, I'm calling about my prescription"})
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        await client.chat.completions.create(model="gpt-4o-mini", messages=messages, max_tokens=1)
        logger.info("OpenAI cache warmed with prescription_status prompt prefix")
    except Exception as e:
        logger.warning(f"OpenAI warmup failed (non-critical): {e}")


class PrescriptionStatusFlow(DialinBaseFlow):
    WORKFLOW_FLOWS = {
        "lab_results": ("clients.demo_clinic_alpha.lab_results.flow_definition", "LabResultsFlow"),
        "scheduling": ("clients.demo_clinic_alpha.patient_scheduling.flow_definition", "PatientSchedulingFlow"),
    }

    RX_FIELDS = ["medication_name", "dosage", "prescribing_physician", "refill_status", "last_filled_date",
                 "next_refill_date", "pharmacy_name", "pharmacy_phone", "pharmacy_address"]

    def _init_domain_state(self):
        state = self.flow_manager.state
        for field in self.RX_FIELDS:
            state[field] = self.call_data.get(field, "")
        state["refills_remaining"] = self.call_data.get("refills_remaining", 0)
        state["prescriptions"] = self.call_data.get("prescriptions", [])
        state.setdefault("callback_confirmed", False)
        state.setdefault("medication_select_attempts", 0)
        state.setdefault("mentioned_medication", None)
        state.setdefault("selected_prescription", None)

    def _get_workflow_type(self) -> str:
        return "prescription_status"

    def _get_global_instructions(self) -> str:
        return f"""You are Jamie, a virtual assistant for {self.organization_name}, answering inbound calls from patients about their prescription refills.

# Voice Conversation Style
You are on a phone call with a patient. Your responses will be converted to speech:
- Speak naturally and warmly, like a helpful clinic staff member
- Keep responses concise and clear—one or two sentences is usually enough
- Use natural acknowledgments: "Of course", "I understand", "Let me check that for you"
- NEVER use bullet points, numbered lists, asterisks, bold, or any markdown formatting
- Say "Got it" or "One moment" instead of robotic phrases

# Handling Speech Recognition
Input is transcribed from speech and may contain errors:
- Silently correct obvious transcription mistakes based on context
- "for too ate" likely means "4 2 8" in a phone number context
- If truly unclear, ask them to repeat naturally: "Sorry, I didn't catch that"

# HIPAA Compliance
- You MUST verify the patient before discussing any prescription information
- Verification is handled by the flow functions - do NOT ask for name/DOB directly
- If verification fails, do not provide any prescription details

# Guardrails
- Never provide medical advice about medications
- If prescription needs doctor approval, explain they will be contacted
- If you don't have information, say so honestly
- Stay on topic: prescription status inquiries only
- If caller is frustrated or asks for a human, offer to transfer them"""

    def _extract_lookup_record(self, patient: dict) -> dict:
        return {
            "patient_id": patient.get("patient_id"),
            "first_name": patient.get("first_name", ""),
            "last_name": patient.get("last_name", ""),
            "date_of_birth": patient.get("date_of_birth", ""),
            "phone_number": patient.get("phone_number", ""),
            "medication_name": patient.get("medication_name", ""),
            "dosage": patient.get("dosage", ""),
            "prescribing_physician": patient.get("prescribing_physician", ""),
            "refill_status": patient.get("refill_status", ""),
            "refills_remaining": patient.get("refills_remaining", 0),
            "last_filled_date": patient.get("last_filled_date", ""),
            "next_refill_date": patient.get("next_refill_date", ""),
            "pharmacy_name": patient.get("pharmacy_name", ""),
            "pharmacy_phone": patient.get("pharmacy_phone", ""),
            "pharmacy_address": patient.get("pharmacy_address", ""),
            "prescriptions": patient.get("prescriptions", []),
        }

    def _populate_domain_state(self, flow_manager: FlowManager, lookup: dict):
        flow_manager.state["medication_name"] = lookup.get("medication_name", "")
        flow_manager.state["dosage"] = lookup.get("dosage", "")
        flow_manager.state["prescribing_physician"] = lookup.get("prescribing_physician", "")
        flow_manager.state["refill_status"] = lookup.get("refill_status", "")
        flow_manager.state["refills_remaining"] = lookup.get("refills_remaining", 0)
        flow_manager.state["last_filled_date"] = lookup.get("last_filled_date", "")
        flow_manager.state["next_refill_date"] = lookup.get("next_refill_date", "")
        flow_manager.state["pharmacy_name"] = lookup.get("pharmacy_name", "")
        flow_manager.state["pharmacy_phone"] = lookup.get("pharmacy_phone", "")
        flow_manager.state["pharmacy_address"] = lookup.get("pharmacy_address", "")
        flow_manager.state["prescriptions"] = lookup.get("prescriptions", [])

    def _route_after_verification(self, flow_manager: FlowManager) -> NodeConfig:
        prescriptions = flow_manager.state.get("prescriptions", [])
        mentioned_medication = flow_manager.state.get("mentioned_medication")
        if len(prescriptions) == 1:
            selected = prescriptions[0]
            flow_manager.state["selected_prescription"] = selected
            flow_manager.state["medication_name"] = selected.get("medication_name", "")
            flow_manager.state["dosage"] = selected.get("dosage", "")
            flow_manager.state["refill_status"] = selected.get("status", selected.get("refill_status", ""))
            flow_manager.state["refills_remaining"] = selected.get("refills_remaining", 0)
            flow_manager.state["next_refill_date"] = selected.get("next_refill_date", "")
            logger.info("Flow: Single prescription - routing to status node")
            return self._route_to_status_node(selected)
        if mentioned_medication and len(prescriptions) > 1:
            matches = self._find_matching_prescriptions(mentioned_medication, prescriptions)
            if len(matches) == 1:
                # Unambiguous match
                selected = matches[0]
                flow_manager.state["selected_prescription"] = selected
                flow_manager.state["medication_name"] = selected.get("medication_name", "")
                flow_manager.state["dosage"] = selected.get("dosage", "")
                flow_manager.state["refill_status"] = selected.get("status", selected.get("refill_status", ""))
                flow_manager.state["refills_remaining"] = selected.get("refills_remaining", 0)
                flow_manager.state["next_refill_date"] = selected.get("next_refill_date", "")
                logger.info(f"Flow: Matched mentioned medication '{mentioned_medication}' - routing to status node")
                return self._route_to_status_node(selected)
            elif len(matches) > 1:
                # Ambiguous - multiple prescriptions match (e.g., "semaglutide" matches Ozempic AND Wegovy)
                logger.info(f"Flow: Multiple matches for '{mentioned_medication}' ({len(matches)} meds) - routing to medication_select")
                return self.create_medication_select_node()
        logger.info("Flow: Multiple prescriptions, no match - routing to medication_select")
        return self.create_medication_select_node()

    def _find_matching_prescriptions(self, mentioned: str, prescriptions: list) -> list[dict]:
        """Returns ALL prescriptions matching the mentioned medication."""
        if not mentioned:
            return []
        matches = []
        mentioned_lower = mentioned.lower().strip()
        for rx in prescriptions:
            if self._rx_matches_mentioned(rx, mentioned_lower):
                matches.append(rx)
        return matches

    def _rx_matches_mentioned(self, rx: dict, mentioned_lower: str) -> bool:
        """Check if a single prescription matches the mentioned medication."""
        rx_name = rx.get("medication_name", "").lower()
        # Direct name match
        if mentioned_lower in rx_name or rx_name in mentioned_lower:
            return True
        # Check aliases from MEDICATIONS schema
        for brand_name, med_info in MEDICATIONS.items():
            if rx_name in brand_name.lower() or brand_name.lower() in rx_name:
                aliases = [a.lower() for a in med_info.get("aliases", [])]
                if mentioned_lower in aliases or any(a in mentioned_lower for a in aliases):
                    return True
                generic = med_info.get("generic", "").lower()
                if generic and (mentioned_lower == generic or generic in mentioned_lower):
                    return True
        return False

    def _get_status_key(self, prescription: dict) -> str:
        status = prescription.get("status", prescription.get("refill_status", "")).lower()
        refills = prescription.get("refills_remaining", 0)
        if status in ("sent", "sent to pharmacy"):
            return "status_sent"
        elif status in ("pending", "pending prior auth", "pending doctor approval", "awaiting prior auth"):
            return "status_pending"
        elif status in ("ready", "ready for pickup"):
            return "status_ready"
        elif status in ("too early", "too early to refill"):
            return "status_too_early"
        elif status in ("renewal", "needs renewal", "expired"):
            return "status_renewal"
        elif refills > 0 or status in ("active", "refills", "refills available"):
            return "status_refills"
        else:
            return "status_pending"

    def _format_status_message(self, status_key: str) -> str:
        state = self.flow_manager.state
        template = PRESCRIPTION_STATUS.get(status_key, {}).get("template", "")
        return template.format(
            medication_name=state.get("medication_name", "your medication"),
            dosage=state.get("dosage", ""),
            pharmacy_name=state.get("pharmacy_name", "your pharmacy"),
            pharmacy_phone=state.get("pharmacy_phone", ""),
            next_refill_date=state.get("next_refill_date", ""),
            refills_remaining=state.get("refills_remaining", 0),
            prescribing_physician=state.get("prescribing_physician", "your doctor"),
        )

    async def _load_domain_data(self, patient_id: str) -> bool:
        if not patient_id:
            return False
        try:
            db = get_async_patient_db()
            patient = await db.find_patient_by_id(patient_id, self.organization_id)
            if not patient:
                logger.warning(f"Flow: Could not load domain data - patient {patient_id} not found")
                return False
            self.flow_manager.state["medication_name"] = patient.get("medication_name", "")
            self.flow_manager.state["dosage"] = patient.get("dosage", "")
            self.flow_manager.state["prescribing_physician"] = patient.get("prescribing_physician", "")
            self.flow_manager.state["refill_status"] = patient.get("refill_status", "")
            self.flow_manager.state["refills_remaining"] = patient.get("refills_remaining", 0)
            self.flow_manager.state["last_filled_date"] = patient.get("last_filled_date", "")
            self.flow_manager.state["next_refill_date"] = patient.get("next_refill_date", "")
            self.flow_manager.state["pharmacy_name"] = patient.get("pharmacy_name", "")
            self.flow_manager.state["pharmacy_phone"] = patient.get("pharmacy_phone", "")
            self.flow_manager.state["pharmacy_address"] = patient.get("pharmacy_address", "")
            self.flow_manager.state["prescriptions"] = patient.get("prescriptions", [])
            logger.info(f"Flow: Loaded domain data for patient {patient_id}")
            return True
        except Exception as e:
            logger.error(f"Flow: Error loading domain data: {e}")
            return False

    def _check_another_medication_schema(self) -> FlowsFunctionSchema:
        return FlowsFunctionSchema(
            name="check_another_medication",
            description="Patient asks about another medication/prescription/refill.",
            properties={},
            required=[],
            handler=self._check_another_medication_handler,
        )

    def get_initial_node(self) -> NodeConfig:
        return self.create_greeting_node()

    def create_greeting_node(self) -> NodeConfig:
        greeting_text = f"Thank you for calling {self.organization_name}. This is Jamie. How can I help you today?"
        return NodeConfig(
            name="greeting",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """# Goal
Route the caller by calling the appropriate function.

# CRITICAL: Do NOT speak - just call a function
- Do NOT generate any speech or text response
- Do NOT ask for medication names or any other information
- Your ONLY job is to call the right function - the next node will handle everything

# Rules - CALL FUNCTION IMMEDIATELY
If caller mentions prescription, refill, medication, or Rx:
→ CALL proceed_to_prescription_status (do NOT say anything)
→ Include mentioned_medication ONLY if caller already named a specific medication

If caller mentions scheduling, appointment, lab results, or blood work:
→ CALL proceed_to_other (do NOT say anything)

If caller mentions billing, insurance, or asks for a human:
→ CALL request_staff (do NOT say anything)

# Examples
Caller: "I'm calling about my Ozempic prescription."
→ Call proceed_to_prescription_status with mentioned_medication="Ozempic"

Caller: "I need to check on a refill."
→ Call proceed_to_prescription_status (NO medication param - do NOT ask)

Caller: "I'd like to check on my prescription stuff."
→ Call proceed_to_prescription_status (NO medication param - do NOT ask)

# Unrecognized Intent
ONLY if truly unclear, ask: "I can help with prescriptions, appointments, or lab results. What are you calling about?"

IMPORTANT: Call a function immediately. Do NOT generate any speech.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="proceed_to_prescription_status",
                    description="Caller asks about prescription/refill/medication. Include mentioned_medication if caller named a specific medication.",
                    properties={
                        "mentioned_medication": {"type": "string", "description": "Medication name if caller mentioned one (e.g., 'Ozempic', 'Wegovy')"},
                    },
                    required=[],
                    handler=self._proceed_to_prescription_status_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_other",
                    description="Caller needs scheduling, lab results, or other non-prescription services.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_other_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": greeting_text}],
        )

    async def create_handoff_entry_node(self, context: str = "") -> NodeConfig:
        context_lower = context.lower()
        if "lisinopril" in context_lower:
            self.flow_manager.state["medication_name"] = "lisinopril"
        if "prior auth" in context_lower:
            self.flow_manager.state["issue_type"] = "prior_authorization"
        if self.flow_manager.state.get("identity_verified"):
            first_name = self.flow_manager.state.get("first_name", "")
            patient_id = self.flow_manager.state.get("patient_id")
            logger.info(f"Flow: Caller already verified as {first_name}, loading domain data")
            await self._load_domain_data(patient_id)
            prescriptions = self.flow_manager.state.get("prescriptions", [])
            if len(prescriptions) == 1:
                return self._route_to_status_node(prescriptions[0])
            elif len(prescriptions) > 1:
                return self.create_medication_select_node()
            return self._route_to_status_node()
        logger.info("Flow: Handoff entry - context stored, proceeding to phone lookup")
        return self.create_patient_lookup_node()

    def create_other_requests_node(self) -> NodeConfig:
        return NodeConfig(
            name="other_requests",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": """# Goal
Route the caller to the appropriate service.

# Routing Table
| Caller needs | Action |
|--------------|--------|
| Scheduling, appointment, follow-up | call route_to_workflow with workflow="scheduling" |
| Lab results, blood work, test results | call route_to_workflow with workflow="lab_results" |
| Billing, insurance, human staff | call request_staff |
| Prescription status (changed mind) | call proceed_to_prescription_status |

# Example Flow
Caller: "I need to schedule a follow-up appointment."
→ Call route_to_workflow with workflow="scheduling", reason="follow-up appointment"

Caller: "Can I check my lab results?"
→ Call route_to_workflow with workflow="lab_results", reason="lab results inquiry"

Caller: "Actually, I wanted to check on my prescription."
→ Call proceed_to_prescription_status

# Unrecognized Intent
If unclear, ask: "I can help with scheduling, lab results, or prescriptions. Which would you like?"

# Guardrails
- Don't ask for personal information yet - the target workflow will handle verification
- Provide brief context in the reason field when routing""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                FlowsFunctionSchema(
                    name="proceed_to_prescription_status",
                    description="Caller wants to check prescription status instead.",
                    properties={
                        "mentioned_medication": {"type": "string", "description": "Medication name if mentioned"},
                    },
                    required=[],
                    handler=self._proceed_to_prescription_status_handler,
                ),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
        )

    def create_medication_select_node(self) -> NodeConfig:
        state = self.flow_manager.state
        prescriptions = state.get("prescriptions", [])
        attempts = state.get("medication_select_attempts", 0)
        rx_list = "\n".join([f"- {rx.get('medication_name', 'Unknown')} ({rx.get('dosage', '')})" for rx in prescriptions])
        if attempts > 0:
            intro = f"I didn't catch that. Your medications on file are:\n{rx_list}\n\nWhich one are you calling about?"
        else:
            intro = f"I see you have multiple medications on file:\n{rx_list}\n\nWhich one are you calling about today?"
        return NodeConfig(
            name="medication_select",
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
Determine which prescription the patient is asking about.

# Medications on File
{rx_list}

# Matching Rules
Match caller's description to medications using:
1. Exact medication name (e.g., "Ozempic")
2. Generic name (e.g., "semaglutide" → Ozempic or Wegovy)
3. Common aliases (e.g., "my weekly shot" → Ozempic/Wegovy/Mounjaro)
4. Descriptions (e.g., "the weight loss one" → match to GLP-1)

# Example Flow
You: "Which medication are you calling about today?"
Patient: "The Ozempic"
→ Call select_medication with medication_name="Ozempic"

Patient: "My weekly shot"
→ If only one injectable on file, call select_medication
→ If multiple, ask: "I see you have Ozempic and Wegovy. Which one?"

# No Match After 2 Attempts
If patient has tried twice and you still can't identify:
→ Call request_staff with reason="couldn't identify medication"

# Guardrails
- Always confirm before selecting
- Use GLP-1 aliases: Ozempic, Wegovy, Mounjaro, Zepbound, Trulicity
- Don't guess—ask for clarification if unclear""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="select_medication",
                    description="Select medication after matching caller's description. Use GLP-1 aliases for matching.",
                    properties={
                        "medication_name": {"type": "string", "description": "Name of the medication (brand name preferred)"},
                    },
                    required=["medication_name"],
                    handler=self._select_medication_handler,
                ),
                self._end_call_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": intro}] if attempts == 0 else None,
        )

    def _build_pharmacy_section(self) -> str:
        """Build pharmacy info section for status node prompts."""
        state = self.flow_manager.state
        pharmacy_name = state.get("pharmacy_name", "")
        pharmacy_phone = state.get("pharmacy_phone", "")
        pharmacy_address = state.get("pharmacy_address", "")

        lines = []
        if pharmacy_name:
            lines.append(f"Name: {pharmacy_name}")
        if pharmacy_address:
            lines.append(f"Address: {pharmacy_address}")
        if pharmacy_phone:
            lines.append(f"Phone: {pharmacy_phone}")

        if not lines:
            return "No pharmacy info on file."
        return "\n".join(lines)

    def _status_base_functions(self) -> list:
        return [
            FlowsFunctionSchema(
                name="proceed_to_completion",
                description="Patient satisfied with status info. Ask if anything else needed.",
                properties={},
                required=[],
                handler=self._proceed_to_completion_handler,
            ),
            self._check_another_medication_schema(),
            self._request_staff_schema(),
        ]

    def create_status_sent_node(self) -> NodeConfig:
        status_message = self._format_status_message("status_sent")
        pharmacy_section = self._build_pharmacy_section()
        return NodeConfig(
            name="status_sent",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. The prescription was sent to the pharmacy.

# What Was Said
"{status_message}"

# Pharmacy Information
{pharmacy_section}

# Answering Questions
- Pharmacy address/location/directions → Provide address above (or say "I don't have the address, but you can call them")
- Pharmacy phone number → Provide phone above
- Pharmacy hours → "I don't have hours on file, but you can call them"

# Caller Actions (call a function)
- Satisfied → call proceed_to_completion
- Pickup problems (wrong med, not ready, issue at pharmacy) → call request_staff with reason
- Another medication → call check_another_medication
- Frustrated/wants human → call request_staff

Do NOT offer to send again - it's already sent.""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_pending_node(self) -> NodeConfig:
        status_message = self._format_status_message("status_pending")
        pharmacy_section = self._build_pharmacy_section()
        return NodeConfig(
            name="status_pending",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. The prescription is pending prior authorization.

# What Was Said
"{status_message}"

# Pharmacy Information
{pharmacy_section}

# Answering Questions
- Pharmacy address/location/directions → Provide address above (or say "I don't have the address, but you can call them")
- Pharmacy phone number → Provide phone above
- When will it be ready → "Once the prior authorization is approved, it will be sent to your pharmacy"

# Caller Actions (call a function)
- Satisfied → call proceed_to_completion
- Urgent/need to expedite → call request_staff with reason="expedite prior auth"
- Another medication → call check_another_medication
- Frustrated/wants human → call request_staff

Do NOT submit another request - one is already pending.""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_ready_node(self) -> NodeConfig:
        status_message = self._format_status_message("status_ready")
        pharmacy_section = self._build_pharmacy_section()
        return NodeConfig(
            name="status_ready",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. The prescription is ready for pickup.

# What Was Said
"{status_message}"

# Pharmacy Information
{pharmacy_section}

# Answering Questions
- Pharmacy address/location/directions → Provide address above (or say "I don't have the address, but you can call them")
- Pharmacy phone number → Provide phone above
- Pharmacy hours → "I don't have hours on file, but you can call them"

# Caller Actions (call a function)
- Satisfied → call proceed_to_completion
- Pickup problems (wrong med, not ready, issue at pharmacy) → call request_staff with reason
- Another medication → call check_another_medication
- Frustrated/wants human → call request_staff""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_too_early_node(self) -> NodeConfig:
        status_message = self._format_status_message("status_too_early")
        pharmacy_section = self._build_pharmacy_section()
        return NodeConfig(
            name="status_too_early",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. It's too early to refill.

# What Was Said
"{status_message}"

# Pharmacy Information
{pharmacy_section}

# Answering Questions
- Pharmacy address/location/directions → Provide address above (or say "I don't have the address, but you can call them")
- Pharmacy phone number → Provide phone above
- When can I refill → Refer to the date mentioned in status message

# Caller Actions (call a function)
- Accepts, satisfied → call proceed_to_completion
- Exception needed (lost, broken, traveling) → call request_staff with reason="early refill exception"
- Another medication → call check_another_medication
- Frustrated/wants human → call request_staff

If they need an early refill, connect with staff who can review exceptions.""",
            }],
            functions=self._status_base_functions(),
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_refills_node(self) -> NodeConfig:
        status_message = self._format_status_message("status_refills")
        state = self.flow_manager.state
        pharmacy_name = state.get("pharmacy_name", "your pharmacy")
        pharmacy_section = self._build_pharmacy_section()
        return NodeConfig(
            name="status_refills",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. Patient has refills available.

# What Was Said
"{status_message}"

# Pharmacy Information
{pharmacy_section}

# Answering Questions
- Pharmacy address/location/directions → Provide address above (or say "I don't have the address, but you can call them")
- Pharmacy phone number → Provide phone above
- Pharmacy hours → "I don't have hours on file, but you can call them"

# Caller Actions (call a function)
- Yes, send refill → call submit_refill
- No thanks → call proceed_to_completion
- Pharmacy change/dosage questions → call request_staff with reason="pharmacy change"
- Another medication → call check_another_medication
- Frustrated/wants human → call request_staff

If patient confirms, submit the refill to {pharmacy_name}.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="submit_refill",
                    description="Patient confirmed - send refill to pharmacy.",
                    properties={},
                    required=[],
                    handler=self._submit_refill_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_completion",
                    description="Patient declines refill or is satisfied.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_completion_handler,
                ),
                self._check_another_medication_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def create_status_renewal_node(self) -> NodeConfig:
        status_message = self._format_status_message("status_renewal")
        state = self.flow_manager.state
        prescribing_physician = state.get("prescribing_physician", "your doctor")
        pharmacy_section = self._build_pharmacy_section()
        return NodeConfig(
            name="status_renewal",
            task_messages=[{
                "role": "system",
                "content": f"""Status has been communicated. Prescription needs renewal.

# What Was Said
"{status_message}"

# Pharmacy Information
{pharmacy_section}

# Answering Questions
- Pharmacy address/location/directions → Provide address above (or say "I don't have the address, but you can call them")
- Pharmacy phone number → Provide phone above
- When will it be ready → "Once {prescribing_physician} approves the renewal, it will be sent to your pharmacy"

# Caller Actions (call a function)
- Yes, submit renewal → call submit_renewal_request
- No thanks → call proceed_to_completion
- Dosage change → call request_staff with reason="dosage change request"
- Another medication → call check_another_medication
- Frustrated/wants human → call request_staff

If patient confirms, submit renewal request to {prescribing_physician}.""",
            }],
            functions=[
                FlowsFunctionSchema(
                    name="submit_renewal_request",
                    description="Patient confirmed - submit renewal request to physician.",
                    properties={},
                    required=[],
                    handler=self._submit_renewal_request_handler,
                ),
                FlowsFunctionSchema(
                    name="proceed_to_completion",
                    description="Patient declines renewal or is satisfied.",
                    properties={},
                    required=[],
                    handler=self._proceed_to_completion_handler,
                ),
                self._check_another_medication_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=[{"type": "tts_say", "text": status_message}],
        )

    def _route_to_status_node(self, prescription: dict = None) -> NodeConfig:
        if prescription:
            state = self.flow_manager.state
            state["medication_name"] = prescription.get("medication_name", "")
            state["dosage"] = prescription.get("dosage", "")
            state["refill_status"] = prescription.get("status", prescription.get("refill_status", ""))
            state["refills_remaining"] = prescription.get("refills_remaining", 0)
            state["next_refill_date"] = prescription.get("next_refill_date", "")
            state["selected_prescription"] = prescription
        status_key = self._get_status_key(prescription or self.flow_manager.state)
        status_node_map = {
            "status_sent": self.create_status_sent_node,
            "status_pending": self.create_status_pending_node,
            "status_ready": self.create_status_ready_node,
            "status_too_early": self.create_status_too_early_node,
            "status_refills": self.create_status_refills_node,
            "status_renewal": self.create_status_renewal_node,
        }
        creator = status_node_map.get(status_key, self.create_status_pending_node)
        return creator()

    def create_completion_node(self) -> NodeConfig:
        state = self.flow_manager.state
        first_name = state.get("first_name", "")
        medication_name = state.get("medication_name", "")
        refill_status = state.get("refill_status", "")
        pharmacy_name = state.get("pharmacy_name", "")
        return NodeConfig(
            name="completion",
            role_messages=[{"role": "system", "content": self._get_global_instructions()}],
            task_messages=[{
                "role": "system",
                "content": f"""# Goal
The prescription inquiry is complete. Check if the caller needs anything else.

# Prescription Info Already Shared (for reference if asked to repeat)
- Medication: {medication_name}
- Status: {refill_status}
- Pharmacy: {pharmacy_name}

# Scenario Handling

If patient says GOODBYE / "that's all" / "bye" / "that's everything" / "no, thanks":
→ Call end_call IMMEDIATELY (do NOT speak - the system will say goodbye)

IMPORTANT: Do NOT say anything before calling end_call. No "You're welcome", no "Take care".
Just call end_call and let the system handle the goodbye message.

NOTE: "Thank you" alone is NOT a goodbye signal - wait for their next response.
Only end_call if they give a clear goodbye like "No, that's all", "Bye", or "That's everything".

If patient asks about ANOTHER MEDICATION, PRESCRIPTION, REFILL:
→ Call check_another_medication IMMEDIATELY

Examples:
- "What about my other prescription?" → check_another_medication
- "Can you also check my [medication]?" → check_another_medication

If patient asks to SCHEDULE an APPOINTMENT or FOLLOW-UP:
→ Call route_to_workflow with workflow="scheduling" IMMEDIATELY
→ Do NOT speak first - the scheduling workflow will handle it

Examples:
- "I need to schedule a follow-up appointment" → route_to_workflow(scheduling)
- "Can I book an appointment with Dr. Williams?" → route_to_workflow(scheduling)

If patient asks about LAB RESULTS or BLOOD WORK:
→ Call route_to_workflow with workflow="lab_results" IMMEDIATELY

If patient asks for a HUMAN or has BILLING questions:
→ Say "Let me connect you with someone who can help."
→ Call request_staff

# Example Responses
Caller: "Actually yes, I need to schedule a follow-up appointment with Dr. Williams."
→ Call route_to_workflow with workflow="scheduling", reason="follow-up to discuss medications"

Caller: "No, that's everything. Thank you!"
→ Call end_call (do NOT speak first)

# Guardrails
- The caller is already verified - no need to re-verify for scheduling or lab results
- Include relevant context in the reason field when routing
- Keep responses brief and warm
- If caller is frustrated or asks for a human, call request_staff""",
            }],
            functions=[
                self._route_to_workflow_schema(),
                self._check_another_medication_schema(),
                self._end_call_schema(),
                self._request_staff_schema(),
            ],
            respond_immediately=False,
            pre_actions=self._completion_pre_actions(),
        )

    def _create_post_workflow_node(self, target_flow, workflow_type: str, entry_method, transition_message: str = "") -> NodeConfig:
        async def proceed_handler(args, flow_manager):
            return None, entry_method()
        return NodeConfig(
            name=f"post_{workflow_type}",
            task_messages=[{"role": "system", "content": f"Call proceed_to_{workflow_type} immediately."}],
            functions=[FlowsFunctionSchema(
                name=f"proceed_to_{workflow_type}",
                description=f"Proceed to {workflow_type}.",
                properties={},
                required=[],
                handler=proceed_handler,
            )],
            respond_immediately=True,
            pre_actions=[{"type": "tts_say", "text": transition_message}] if transition_message else None,
        )

    async def _proceed_to_prescription_status_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str | None, NodeConfig]:
        mentioned_medication = args.get("mentioned_medication", "")
        if mentioned_medication:
            flow_manager.state["mentioned_medication"] = mentioned_medication
            logger.info(f"Flow: Proceeding to prescription status with mentioned medication: {mentioned_medication}")
        else:
            logger.info("Flow: Proceeding to prescription status")
        return None, self.create_patient_lookup_node()

    async def _proceed_to_other_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        logger.info("Flow: Proceeding to other requests")
        return None, self.create_other_requests_node()

    async def _select_medication_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        medication_name = args.get("medication_name", "").strip()
        logger.info(f"Flow: Selected medication: {medication_name}")
        prescriptions = flow_manager.state.get("prescriptions", [])
        matches = self._find_matching_prescriptions(medication_name, prescriptions)
        selected_rx = matches[0] if matches else None
        if not selected_rx:
            for rx in prescriptions:
                if medication_name.lower() in rx.get("medication_name", "").lower():
                    selected_rx = rx
                    break
        if not selected_rx:
            flow_manager.state["medication_select_attempts"] = flow_manager.state.get("medication_select_attempts", 0) + 1
            if flow_manager.state["medication_select_attempts"] >= 2:
                logger.info("Flow: Couldn't identify medication after 2 attempts - transferring to staff")
                return await self._request_staff_handler({"reason": "couldn't identify medication"}, flow_manager)
            return f"I couldn't find a prescription matching '{medication_name}'. Could you clarify which medication?", self.create_medication_select_node()
        flow_manager.state["medication_name"] = selected_rx.get("medication_name", "")
        flow_manager.state["dosage"] = selected_rx.get("dosage", "")
        flow_manager.state["prescribing_physician"] = selected_rx.get("prescribing_physician", "")
        flow_manager.state["refill_status"] = selected_rx.get("status", selected_rx.get("refill_status", ""))
        flow_manager.state["refills_remaining"] = selected_rx.get("refills_remaining", 0)
        flow_manager.state["last_filled_date"] = selected_rx.get("last_filled_date", "")
        flow_manager.state["next_refill_date"] = selected_rx.get("next_refill_date", "")
        flow_manager.state["selected_prescription"] = selected_rx
        return None, self._route_to_status_node(selected_rx)

    async def _submit_refill_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        pharmacy_name = args.get("pharmacy_name", flow_manager.state.get("pharmacy_name", "your pharmacy"))
        medication_name = flow_manager.state.get("medication_name", "")
        logger.info(f"Flow: Submitting refill for {medication_name} to {pharmacy_name}")
        patient_id = flow_manager.state.get("patient_id")
        if patient_id:
            await self._try_db_update(
                patient_id, "update_patient",
                {"refill_requested": True, "refill_pharmacy": pharmacy_name},
                error_msg="Error saving refill request"
            )
        self._reset_anything_else_count()
        return f"I've submitted the refill request to {pharmacy_name}. They should have it ready within 2 to 4 hours.", self.create_completion_node()

    async def _submit_renewal_request_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        physician = flow_manager.state.get("prescribing_physician", "your doctor")
        medication_name = flow_manager.state.get("medication_name", "")
        pharmacy_name = flow_manager.state.get("pharmacy_name", "your pharmacy")
        logger.info(f"Flow: Submitting renewal request for {medication_name} to {physician}")
        patient_id = flow_manager.state.get("patient_id")
        if patient_id:
            await self._try_db_update(
                patient_id, "update_patient",
                {"renewal_requested": True, "renewal_physician": physician},
                error_msg="Error saving renewal request"
            )
        self._reset_anything_else_count()
        return f"I've submitted the refill request to {physician} for review. Once approved, the prescription will be sent to {pharmacy_name}. You should hear back within 1 to 2 business days.", self.create_completion_node()

    async def _check_another_medication_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[str, NodeConfig]:
        logger.info("Flow: Checking another prescription")
        prescriptions = flow_manager.state.get("prescriptions", [])
        if len(prescriptions) > 1:
            return "", self.create_medication_select_node()
        else:
            self._reset_anything_else_count()
            return "I only see one prescription on file for you. Is there something else I can help you with?", self.create_completion_node()

    async def _proceed_to_completion_handler(self, args: Dict[str, Any], flow_manager: FlowManager) -> tuple[None, NodeConfig]:
        logger.info("Flow: Proceeding to completion")
        self._reset_anything_else_count()
        return None, self.create_completion_node()
