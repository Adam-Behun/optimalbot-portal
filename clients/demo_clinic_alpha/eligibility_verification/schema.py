# Flow state variables (runtime, not persisted to DB)
FLOW_STATE = {
    # Patient identification (from patient_data)
    "patient_id": None,
    "patient_name": None,
    "date_of_birth": None,
    "insurance_member_id": None,
    "insurance_company_name": None,
    "insurance_phone": None,

    # Caller/Provider information (from patient_data)
    "caller_name": None,
    "caller_last_initial": None,
    "facility_name": None,
    "tax_id": None,
    "provider_name": None,
    "provider_npi": None,
    "provider_call_back_phone": None,

    # Service information (from patient_data)
    "cpt_code": None,
    "place_of_service": None,
    "date_of_service": None,

    # Plan info (collected during call)
    "network_status": None,
    "plan_type": None,
    "plan_effective_date": None,
    "plan_term_date": None,

    # CPT coverage (collected during call)
    "cpt_covered": None,
    "copay_amount": None,
    "coinsurance_percent": None,
    "deductible_applies": None,
    "prior_auth_required": None,
    "telehealth_covered": None,

    # Accumulators (collected during call)
    "deductible_individual": None,
    "deductible_individual_met": None,
    "deductible_family": None,
    "deductible_family_met": None,
    "oop_max_individual": None,
    "oop_max_individual_met": None,
    "oop_max_family": None,
    "oop_max_family_met": None,
    "allowed_amount": None,

    # Call outcome (collected during call)
    "rep_first_name": None,
    "rep_last_initial": None,
    "reference_number": None,
    "additional_notes": None,
}

WORKFLOW_SCHEMA = {
    "enabled": True,
    "display_name": "Eligibility Verification",
    "description": "Outbound calls to insurance companies for eligibility and benefits verification",
    "call_direction": "dial-out",
    "dial_out_phone_field": "insurance_phone",
    "record_type": "patient",  # Patients are the verification subjects
    "patient_schema": {
        "fields": [
            {"key": "patient_name", "label": "Patient Name", "type": "string", "required": True, "display_in_list": True, "display_order": 1, "display_priority": "mobile"},
            {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": True, "display_in_list": True, "display_order": 2, "display_priority": "tablet"},
            {"key": "insurance_member_id", "label": "Member ID", "type": "string", "required": True, "display_in_list": True, "display_order": 3, "display_priority": "mobile"},
            {"key": "insurance_company_name", "label": "Insurance Company", "type": "string", "required": True, "display_in_list": False, "display_order": 4},
            {"key": "insurance_phone", "label": "Insurance Phone", "type": "phone", "required": True, "display_in_list": False, "display_order": 5},
            {"key": "caller_name", "label": "Caller First Name", "type": "string", "required": True, "display_in_list": False, "display_order": 11},
            {"key": "caller_last_initial", "label": "Caller Last Initial", "type": "string", "required": True, "display_in_list": False, "display_order": 12},
            {"key": "facility_name", "label": "Facility Name", "type": "string", "required": True, "display_in_list": False, "display_order": 13},
            {"key": "tax_id", "label": "Tax ID", "type": "string", "required": True, "display_in_list": False, "display_order": 14},
            {"key": "provider_name", "label": "Provider Name", "type": "string", "required": True, "display_in_list": False, "display_order": 15},
            {"key": "provider_npi", "label": "Provider NPI", "type": "string", "required": True, "display_in_list": False, "display_order": 16},
            {"key": "provider_call_back_phone", "label": "Provider Callback Phone", "type": "phone", "required": True, "display_in_list": False, "display_order": 17},
            {"key": "cpt_code", "label": "CPT Code", "type": "string", "required": True, "display_in_list": True, "display_order": 21, "display_priority": "tablet"},
            {"key": "place_of_service", "label": "Place of Service", "type": "select", "options": ["Home", "Office", "Outpatient Hospital", "Inpatient Hospital", "Other"], "required": True, "display_in_list": False, "display_order": 22},
            {"key": "date_of_service", "label": "Date of Service", "type": "string", "required": False, "display_in_list": False, "display_order": 23},
            {"key": "network_status", "label": "Network Status", "type": "select", "options": ["In-Network", "Out-of-Network", "Unknown"], "required": False, "display_in_list": True, "display_order": 31, "display_priority": "desktop", "computed": True},
            {"key": "plan_type", "label": "Plan Type", "type": "select", "options": ["PPO", "HMO", "POS", "EPO", "Other", "Unknown"], "required": False, "display_in_list": True, "display_order": 32, "display_priority": "desktop", "computed": True},
            {"key": "plan_effective_date", "label": "Plan Effective Date", "type": "string", "required": False, "display_in_list": False, "display_order": 33, "computed": True},
            {"key": "plan_term_date", "label": "Plan Term Date", "type": "string", "required": False, "display_in_list": False, "display_order": 34, "computed": True},
            {"key": "cpt_covered", "label": "CPT Covered", "type": "select", "options": ["Yes", "No", "Unknown"], "required": False, "display_in_list": True, "display_order": 41, "display_priority": "desktop", "computed": True},
            {"key": "copay_amount", "label": "Copay", "type": "string", "required": False, "display_in_list": True, "display_order": 42, "display_priority": "desktop", "computed": True},
            {"key": "coinsurance_percent", "label": "Coinsurance", "type": "string", "required": False, "display_in_list": False, "display_order": 43, "computed": True},
            {"key": "deductible_applies", "label": "Deductible Applies", "type": "select", "options": ["Yes", "No", "Unknown"], "required": False, "display_in_list": False, "display_order": 44, "computed": True},
            {"key": "prior_auth_required", "label": "Prior Auth Required", "type": "select", "options": ["Yes", "No", "Unknown"], "required": False, "display_in_list": True, "display_order": 45, "display_priority": "desktop", "computed": True},
            {"key": "telehealth_covered", "label": "Telehealth Covered", "type": "select", "options": ["Yes", "No", "Unknown"], "required": False, "display_in_list": False, "display_order": 46, "computed": True},
            {"key": "deductible_individual", "label": "Individual Deductible", "type": "string", "required": False, "display_in_list": False, "display_order": 51, "computed": True},
            {"key": "deductible_individual_met", "label": "Individual Deductible Met", "type": "string", "required": False, "display_in_list": False, "display_order": 52, "computed": True},
            {"key": "deductible_family", "label": "Family Deductible", "type": "string", "required": False, "display_in_list": False, "display_order": 53, "computed": True},
            {"key": "deductible_family_met", "label": "Family Deductible Met", "type": "string", "required": False, "display_in_list": False, "display_order": 54, "computed": True},
            {"key": "oop_max_individual", "label": "Individual OOP Max", "type": "string", "required": False, "display_in_list": False, "display_order": 61, "computed": True},
            {"key": "oop_max_individual_met", "label": "Individual OOP Met", "type": "string", "required": False, "display_in_list": False, "display_order": 62, "computed": True},
            {"key": "oop_max_family", "label": "Family OOP Max", "type": "string", "required": False, "display_in_list": False, "display_order": 63, "computed": True},
            {"key": "oop_max_family_met", "label": "Family OOP Met", "type": "string", "required": False, "display_in_list": False, "display_order": 64, "computed": True},
            {"key": "allowed_amount", "label": "Allowed Amount", "type": "string", "required": False, "display_in_list": False, "display_order": 65, "computed": True},
            {"key": "rep_first_name", "label": "Rep First Name", "type": "string", "required": False, "display_in_list": False, "display_order": 71, "computed": True},
            {"key": "rep_last_initial", "label": "Rep Last Initial", "type": "string", "required": False, "display_in_list": False, "display_order": 72, "computed": True},
            {"key": "reference_number", "label": "Reference #", "type": "string", "required": False, "display_in_list": True, "display_order": 73, "display_priority": "desktop", "computed": True},
            {"key": "additional_notes", "label": "Additional Notes", "type": "string", "required": False, "display_in_list": False, "display_order": 74, "computed": True},
        ]
    }
}
