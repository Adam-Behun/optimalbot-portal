// Patient interface matching the MongoDB schema - flat field storage
export interface Patient {
  patient_id: string; // MongoDB ObjectID as string
  organization_id: string;
  workflow: string; // Workflow this patient belongs to

  // All other fields are stored flat (dynamic based on workflow schema)
  [key: string]: any;

  // System fields
  call_status: 'Not Started' | 'Dialing' | 'In Progress' | 'Completed' | 'Failed' | 'Supervisor Dialed';
  call_transcript?: string; // JSON string of transcript array
  last_call_session_id?: string;
  created_at?: string;
  updated_at?: string;
}

// Schema field definition for dynamic form generation
export interface SchemaField {
  key: string;                    // Field name in patient document
  label: string;                  // Display label
  type: 'string' | 'date' | 'datetime' | 'time' | 'phone' | 'select' | 'text';
  required: boolean;
  display_in_list: boolean;       // Show in patient list table
  display_order: number;          // Sort order for UI
  display_priority?: 'mobile' | 'tablet' | 'desktop';  // Responsive visibility (mobile=all, tablet=≥768px, desktop=≥1024px)
  options?: string[];             // For select fields
  default?: string;               // Default value
  computed?: boolean;             // True if bot updates (not user-editable)
}

// Workflow configuration
export interface WorkflowConfig {
  enabled: boolean;
  display_name: string;           // Human-readable workflow name
  description: string;            // Workflow description
  patient_schema: {
    fields: SchemaField[];
  };
}

// Organization configuration
export interface Organization {
  id: string;
  name: string;
  slug: string;
  subdomain: string;
  branding: {
    company_name: string;
    logo_url?: string;
    primary_color?: string;
  };
  workflows: Record<string, WorkflowConfig>;
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
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  type?: 'transcript' | 'ivr' | 'ivr_action' | 'ivr_summary' | 'transfer' | 'triage';
}

// Session - unified call record (every call has a session)
export interface Session {
  _id: string;
  session_id: string;
  organization_id: string;
  workflow: string;
  status: 'starting' | 'running' | 'completed' | 'failed' | 'transferred';
  caller_phone?: string;
  called_phone?: string;
  call_transcript?: {
    messages: TranscriptMessage[];
    message_count: number;
  };
  // Link to patient (optional - may be null for unverified dial-in)
  patient_id?: string;
  identity_verified?: boolean;
  // Mainline-specific metadata
  caller_name?: string;
  call_type?: string;
  call_reason?: string;
  routed_to?: string;
  // Timestamps
  created_at: string;
  updated_at?: string;
  completed_at?: string;
}

export interface SessionsResponse {
  sessions: Session[];
  total_count: number;
}

// Authentication types
export interface LoginRequest {
  email: string;
  password: string;
  organization_slug?: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  email: string;
  organization: Organization | null;
  is_super_admin?: boolean;
}