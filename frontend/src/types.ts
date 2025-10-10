// Patient interface matching the MongoDB schema
export interface Patient {
  patient_id: string; // MongoDB ObjectID as string
  patient_name: string;
  date_of_birth: string;
  insurance_member_id?: string;
  insurance_company_name: string;
  facility_name: string;
  cpt_code: string;
  provider_npi: string;
  appointment_time?: string;
  prior_auth_status: string;
  reference_number?: string;
  [key: string]: any;
  
  // Call-related fields (added in models.py)
  call_status: 'Not Started' | 'In Progress' | 'Completed';
  insurance_phone_number?: string;
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

// Fixed to match actual backend response from app.py
export interface StartCallResponse {
  status: string;
  session_id: string;
  patient_id: string;
  patient_name: string;
  facility_name: string;
  phone_number: string;
  room_name: string;
  dialout_id?: string; // Changed from user_token and room_url
  message: string;
}

// Form data for adding a patient
export interface AddPatientFormData {
  patient_name: string;
  date_of_birth: string;
  insurance_member_id?: string;
  insurance_company_name: string;
  facility_name: string;
  cpt_code: string;
  provider_npi: string;
  appointment_time?: string;
}

// Transcript message structure
export interface TranscriptMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  type: string;
}