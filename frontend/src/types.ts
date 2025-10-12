// Patient interface matching the MongoDB schema
export interface Patient {
  patient_id: string; // MongoDB ObjectID as string
  patient_name: string;
  date_of_birth: string;
  insurance_member_id: string;
  insurance_company_name: string;
  insurance_phone: string; // Format: +1234567890
  facility_name: string;
  cpt_code: string;
  provider_npi: string;
  provider_name: string;
  appointment_time: string;
  prior_auth_status: string;
  reference_number?: string;
  [key: string]: any;
  
  // Call-related fields
  call_status: 'Not Started' | 'In Progress' | 'Completed';
  call_transcript?: string; // JSON string of transcript array
  
  // Timestamps
  created_at?: string;
  updated_at?: string;
}

// API response types
export interface PatientsResponse {
  patients: Patient[];
  total_count: number;
  skip: number;
  limit: number;
}

export interface PatientResponse {
  status: string;
  patient: Patient;
}

export interface AddPatientResponse {
  status: string;
  patient_id: string;
  patient_name: string;
  message: string;
}

export interface StartCallResponse {
  status: string;
  session_id: string;
  patient_id: string;
  patient_name: string;
  facility_name: string;
  phone_number: string;
  room_name: string;
  dialout_id?: string;
  message: string;
}

// Unified form data for adding patient(s) - ALL FIELDS REQUIRED
export interface AddPatientFormData {
  patient_name: string;
  date_of_birth: string;
  insurance_member_id: string;
  insurance_company_name: string;
  insurance_phone: string; // Format: +1234567890
  facility_name: string;
  cpt_code: string;
  provider_npi: string;
  provider_name: string;
  appointment_time: string;
}

// Bulk add response
export interface BulkAddResponse {
  status: string;
  success_count: number;
  failed_count: number;
  errors?: Array<{
    row: number;
    patient_name?: string;
    error: string;
  }>;
  message: string;
}

// Transcript message structure
export interface TranscriptMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  type: string;
}