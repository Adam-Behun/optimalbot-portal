WORKFLOW_SCHEMA = {
    "enabled": True,
    "display_name": "Main Line",
    "description": "Main phone line - answer questions or route to departments",
    "call_direction": "dial-in",
    "patient_schema": {
        "fields": [
            {"key": "call_type", "label": "Call Type", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},
            {"key": "call_reason", "label": "Call Reason", "type": "string", "required": False, "display_in_list": True, "display_order": 2, "computed": True},
            {"key": "caller_name", "label": "Caller Name", "type": "string", "required": False, "display_in_list": True, "display_order": 3, "computed": True},
            {"key": "caller_phone_number", "label": "Caller Phone", "type": "phone", "required": False, "display_in_list": True, "display_order": 4, "computed": True},
            {"key": "routed_to", "label": "Routed To", "type": "string", "required": False, "display_in_list": True, "display_order": 5, "computed": True},
            {"key": "resolution", "label": "Resolution", "type": "string", "required": False, "display_in_list": False, "display_order": 6, "computed": True},
            {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": True, "display_order": 7, "computed": True},
            {"key": "call_duration", "label": "Duration", "type": "string", "required": False, "display_in_list": False, "display_order": 8, "computed": True},
            {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 9, "computed": True},
            {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 10, "computed": True},
        ]
    }
}
