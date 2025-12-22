WORKFLOW_SCHEMA = {
    "enabled": True,
    "display_name": "Patient Scheduling",
    "description": "Inbound calls for appointment scheduling",
    "call_direction": "dial-in",
    "patient_schema": {
        "fields": [
            # Patient identity (common fields for cross-workflow support)
            {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},
            {"key": "first_name", "label": "First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 2, "computed": True},
            {"key": "last_name", "label": "Last Name", "type": "string", "required": False, "display_in_list": False, "display_order": 3, "computed": True},
            {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 4, "computed": True},
            {"key": "phone_number", "label": "Phone Number", "type": "phone", "required": False, "display_in_list": True, "display_order": 5, "computed": True},
            {"key": "email", "label": "Email", "type": "string", "required": False, "display_in_list": False, "display_order": 6, "computed": True},

            # Appointment details (workflow-specific)
            {"key": "appointment_type", "label": "Appointment Type", "type": "string", "required": False, "display_in_list": True, "display_order": 7, "computed": True},
            {"key": "appointment_date", "label": "Appointment Date", "type": "date", "required": False, "display_in_list": True, "display_order": 8, "computed": True},
            {"key": "appointment_time", "label": "Appointment Time", "type": "time", "required": False, "display_in_list": False, "display_order": 9, "computed": True},
            {"key": "appointment_reason", "label": "Appointment Reason", "type": "string", "required": False, "display_in_list": False, "display_order": 10, "computed": True},

            # Call outcome (recorded during call)
            {"key": "identity_verified", "label": "Identity Verified", "type": "boolean", "required": False, "display_in_list": False, "display_order": 11, "computed": True},
            {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 12, "computed": True},
            {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 13, "computed": True},
            {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 14, "computed": True},
            {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 15, "computed": True},
        ]
    }
}
