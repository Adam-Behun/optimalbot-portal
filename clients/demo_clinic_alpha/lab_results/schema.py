WORKFLOW_SCHEMA = {
    "enabled": True,
    "display_name": "Lab Results",
    "description": "Inbound calls for lab result inquiries",
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

            # Lab order (from clinic database - what we tell the patient)
            {"key": "test_type", "label": "Test Type", "type": "string", "required": False, "display_in_list": True, "display_order": 7, "display_priority": "tablet", "computed": False},
            {"key": "test_date", "label": "Test Date", "type": "date", "required": False, "display_in_list": True, "display_order": 8, "display_priority": "desktop", "computed": False},
            {"key": "ordering_physician", "label": "Ordering Physician", "type": "string", "required": False, "display_in_list": False, "display_order": 9, "computed": False},
            {"key": "results_status", "label": "Results Status", "type": "string", "required": False, "display_in_list": True, "display_order": 10, "display_priority": "mobile", "computed": False},
            {"key": "results_summary", "label": "Results Summary", "type": "string", "required": False, "display_in_list": False, "display_order": 11, "computed": False},
            {"key": "provider_review_required", "label": "Provider Review Required", "type": "boolean", "required": False, "display_in_list": False, "display_order": 12, "computed": False},
            {"key": "callback_timeframe", "label": "Callback Timeframe", "type": "string", "required": False, "display_in_list": False, "display_order": 13, "computed": False},

            # Call outcome (recorded during call)
            {"key": "identity_verified", "label": "Identity Verified", "type": "boolean", "required": False, "display_in_list": False, "display_order": 14, "computed": True},
            {"key": "results_communicated", "label": "Results Communicated", "type": "boolean", "required": False, "display_in_list": False, "display_order": 15, "computed": True},
            {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 16, "computed": True},
            {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 17, "computed": True},
            {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 18, "computed": True},
            {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 19, "computed": True},
        ]
    }
}
