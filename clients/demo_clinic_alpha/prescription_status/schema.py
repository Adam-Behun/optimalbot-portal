# Prescription status values - maps to status_* nodes in flow
PRESCRIPTION_STATUS = {
    "status_sent": {
        "label": "Sent to Pharmacy",
        "template": "Your {medication_name} {dosage} has been sent to {pharmacy_name}. You can reach them at {pharmacy_phone} to check if it's ready for pickup."
    },
    "status_pending": {
        "label": "Pending Prior Auth",
        "template": "Your {medication_name} is awaiting prior authorization approval. This typically takes 2-3 business days. We'll call you once it's approved."
    },
    "status_ready": {
        "label": "Ready for Pickup",
        "template": "Your {medication_name} {dosage} is ready for pickup at {pharmacy_name}."
    },
    "status_too_early": {
        "label": "Too Early to Refill",
        "template": "It's a bit early to refill your {medication_name}. Your insurance will cover the next fill on {next_refill_date}."
    },
    "status_refills": {
        "label": "Refills Available",
        "template": "You have {refills_remaining} refills remaining for your {medication_name} {dosage}. Would you like me to send one to {pharmacy_name}?"
    },
    "status_renewal": {
        "label": "Needs Renewal",
        "template": "Your {medication_name} prescription needs a new prior authorization. Would you like me to submit the request to {prescribing_physician}?"
    },
}

# GLP-1 medications with STT aliases for matching
MEDICATIONS = {
    "Ozempic": {
        "generic": "semaglutide",
        "aliases": ["ozempic", "oh-zempic", "ozempik", "semaglutide", "my weekly shot", "diabetes shot"]
    },
    "Wegovy": {
        "generic": "semaglutide",
        "aliases": ["wegovy", "we-govy", "wegovi", "weight loss shot", "semaglutide"]
    },
    "Mounjaro": {
        "generic": "tirzepatide",
        "aliases": ["mounjaro", "moun-jaro", "mounjarro", "tirzepatide", "the new one"]
    },
    "Zepbound": {
        "generic": "tirzepatide",
        "aliases": ["zepbound", "zep-bound", "tirzepatide", "weight loss injection"]
    },
    "Trulicity": {
        "generic": "dulaglutide",
        "aliases": ["trulicity", "true-licity", "dulaglutide", "weekly diabetes medication"]
    },
}

# Flow state variables (runtime, not persisted to DB)
FLOW_STATE = {
    # Identity
    "patient_id": None,
    "patient_name": None,
    "first_name": None,
    "last_name": None,
    "date_of_birth": None,
    "phone_number": None,
    # Pharmacy
    "pharmacy_name": None,
    "pharmacy_phone": None,
    "pharmacy_address": None,
    # Prescriptions
    "prescriptions": [],  # Array of prescription objects
    "selected_prescription": None,  # Current prescription being discussed
    "mentioned_medication": None,  # Medication volunteered before medication_select
    # Flags
    "identity_verified": False,
    "routed_to": None,
    # Retry counters (reset per attempt type)
    "lookup_attempts": 0,  # max 2
    "medication_select_attempts": 0,  # max 2
    "transfer_attempts": 0,  # max 2
    # Completion flow control (base class)
    "anything_else_count": 0,  # 0=not asked, 1=asked once (don't ask again)
}

WORKFLOW_SCHEMA = {
    "enabled": True,
    "display_name": "Prescription Status",
    "description": "Inbound calls for prescription refill inquiries",
    "call_direction": "dial-in",
    "record_type": "patient",  # Patients are verified in this workflow
    "patient_schema": {
        "fields": [
            # Patient identity (common fields for cross-workflow support)
            {"key": "patient_name", "label": "Patient Name", "type": "string", "required": True, "display_in_list": True, "display_order": 1, "display_priority": "mobile", "computed": False},
            {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 2, "computed": False},
            {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 3, "computed": False},
            {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": True, "display_in_list": True, "display_order": 4, "display_priority": "desktop", "computed": False},
            {"key": "phone_number", "label": "Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 5, "computed": False},

            # Prescription record (from clinic database - what we tell the patient)
            {"key": "medication_name", "label": "Medication", "type": "string", "required": True, "display_in_list": True, "display_order": 7, "display_priority": "mobile", "computed": False},
            {"key": "dosage", "label": "Dosage", "type": "string", "required": False, "display_in_list": False, "display_order": 8, "computed": False},
            {"key": "prescribing_physician", "label": "Prescribing Physician", "type": "string", "required": False, "display_in_list": False, "display_order": 9, "computed": False},
            {"key": "refill_status", "label": "Refill Status", "type": "string", "required": False, "display_in_list": True, "display_order": 10, "display_priority": "mobile", "computed": False},
            {"key": "refills_remaining", "label": "Refills Remaining", "type": "number", "required": False, "display_in_list": False, "display_order": 11, "computed": False},
            {"key": "last_filled_date", "label": "Last Filled", "type": "date", "required": False, "display_in_list": False, "display_order": 12, "computed": False},
            {"key": "next_refill_date", "label": "Next Refill Date", "type": "date", "required": False, "display_in_list": False, "display_order": 13, "computed": False},

            # Pharmacy (from clinic database)
            {"key": "pharmacy_name", "label": "Pharmacy", "type": "string", "required": False, "display_in_list": True, "display_order": 14, "display_priority": "desktop", "computed": False},
            {"key": "pharmacy_phone", "label": "Pharmacy Phone", "type": "phone", "required": False, "display_in_list": False, "display_order": 15, "computed": False},
            {"key": "pharmacy_address", "label": "Pharmacy Address", "type": "string", "required": False, "display_in_list": False, "display_order": 16, "computed": False},

            # Call outcome (recorded during call)
            {"key": "identity_verified", "label": "Identity Verified", "type": "boolean", "required": False, "display_in_list": False, "display_order": 17, "computed": True},
            {"key": "status_communicated", "label": "Status Communicated", "type": "boolean", "required": False, "display_in_list": False, "display_order": 18, "computed": True},
            {"key": "refill_requested", "label": "Refill Requested", "type": "boolean", "required": False, "display_in_list": False, "display_order": 19, "computed": True},
            {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 20, "computed": True},
            {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 21, "computed": True},
            {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 22, "computed": True},
            {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 23, "computed": True},
        ]
    }
}
