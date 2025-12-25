WORKFLOW_SCHEMA = {
    "enabled": True,
    "display_name": "Prescription Status",
    "description": "Inbound calls for prescription refill inquiries",
    "call_direction": "dial-in",
    "record_type": "patient",  # Patients are verified in this workflow
    "patient_schema": {
        "fields": [
            # Patient identity (common fields for cross-workflow support)
            {"key": "patient_name", "label": "Patient Name", "type": "string", "required": True, "display_in_list": True, "display_order": 1, "computed": False},
            {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 2, "computed": False},
            {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 3, "computed": False},
            {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": True, "display_in_list": True, "display_order": 4, "computed": False},
            {"key": "phone_number", "label": "Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 5, "computed": False},
            {"key": "medical_record_number", "label": "MRN", "type": "string", "required": False, "display_in_list": False, "display_order": 6, "computed": False},

            # Prescription record (from clinic database - what we tell the patient)
            {"key": "medication_name", "label": "Medication", "type": "string", "required": True, "display_in_list": True, "display_order": 7, "computed": False},
            {"key": "dosage", "label": "Dosage", "type": "string", "required": False, "display_in_list": False, "display_order": 8, "computed": False},
            {"key": "prescribing_physician", "label": "Prescribing Physician", "type": "string", "required": False, "display_in_list": False, "display_order": 9, "computed": False},
            {"key": "refill_status", "label": "Refill Status", "type": "string", "required": False, "display_in_list": True, "display_order": 10, "computed": False},
            {"key": "refills_remaining", "label": "Refills Remaining", "type": "number", "required": False, "display_in_list": False, "display_order": 11, "computed": False},
            {"key": "last_filled_date", "label": "Last Filled", "type": "date", "required": False, "display_in_list": False, "display_order": 12, "computed": False},
            {"key": "next_refill_date", "label": "Next Refill Date", "type": "date", "required": False, "display_in_list": False, "display_order": 13, "computed": False},

            # Pharmacy (from clinic database)
            {"key": "pharmacy_name", "label": "Pharmacy", "type": "string", "required": False, "display_in_list": True, "display_order": 14, "computed": False},
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
