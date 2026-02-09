import axios from 'axios';
import {
  Patient,
  PatientsResponse,
  AddPatientResponse,
  StartCallResponse,
  BulkAddResponse,
  AuthResponse,
  Session,
  SessionsResponse,
  TranscriptMessage
} from './types';
import { removeAuthToken, getAuthToken, emitLogoutEvent } from './lib/auth';

// Use Vite environment variable (empty string uses proxy in dev, relative URLs in production)
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor to include JWT token
api.interceptors.request.use(
  (config) => {
    const token = getAuthToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Add response interceptor to handle 401 (session expired)
api.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    // If we get a 401 Unauthorized response (token expired or invalid)
    if (error.response?.status === 401) {
      // HIPAA Compliance: Clear all authentication data
      removeAuthToken();
      emitLogoutEvent(); // Triggers context cleanup via SessionCleanup

      // Redirect to landing page
      window.location.href = '/';

      // Return a rejected promise with a user-friendly message
      return Promise.reject(new Error('Your session has expired. Please log in again.'));
    }

    return Promise.reject(error);
  }
);

// GET /patients - Fetch all patients, optionally filtered by workflow
export const getPatients = async (workflow?: string): Promise<Patient[]> => {
  const params = workflow ? { workflow } : {};
  const response = await api.get<PatientsResponse>('/patients', { params });
  return response.data.patients;
};

// GET /patients/:id - Fetch single patient by ObjectID
export const getPatient = async (patientId: string) => {
  const response = await api.get(`/patients/${patientId}`);
  return response.data.patient;
};

// POST /patients - Add a new patient
export const addPatient = async (patientData: Record<string, any>): Promise<AddPatientResponse> => {
  const response = await api.post<AddPatientResponse>('/patients', patientData);
  return response.data;
};

// POST /patients/bulk - Add multiple patients
export const addPatientsBulk = async (patients: Record<string, any>[]): Promise<BulkAddResponse> => {
  const response = await api.post<BulkAddResponse>('/patients/bulk', { patients });
  return response.data;
};

// DELETE /patients/:id - Delete a patient
export const deletePatient = async (patientId: string): Promise<void> => {
  await api.delete(`/patients/${patientId}`);
};

// PUT /patients/:id - Update a patient
export const updatePatient = async (patientId: string, patientData: Record<string, any>): Promise<void> => {
  await api.put(`/patients/${patientId}`, patientData);
};

// GET /sessions - Fetch sessions, optionally filtered by workflow or patient
export const getSessions = async (workflow?: string, patientId?: string): Promise<Session[]> => {
  const params: Record<string, string> = {};
  if (workflow) params.workflow = workflow;
  if (patientId) params.patient_id = patientId;
  const response = await api.get<SessionsResponse>('/sessions', { params });
  return response.data.sessions;
};

// GET /sessions/:id - Fetch single session
export const getSession = async (sessionId: string): Promise<Session> => {
  const response = await api.get(`/sessions/${sessionId}`);
  return response.data.session;
};

// DELETE /sessions/:id - Delete a session
export const deleteSession = async (sessionId: string): Promise<void> => {
  await api.delete(`/sessions/${sessionId}`);
};

// GET /call/:sessionId/transcript - Get call transcript
export const getCallTranscript = async (sessionId: string): Promise<{ messages: TranscriptMessage[] }> => {
  console.log('Fetching transcript for session:', sessionId);
  const response = await api.get(`/call/${sessionId}/transcript`);
  console.log('Transcript API response:', response.data);
  return { messages: (response.data.transcripts || []) as TranscriptMessage[] };
};

// Get call history for a patient
export const getPatientCallHistory = async (patientId: string): Promise<Session[]> => {
  return getSessions(undefined, patientId);
};

// POST /start-call - Start a call for a patient
export const startCall = async (patientId: string, phoneNumber: string, clientName: string): Promise<StartCallResponse> => {
  const response = await api.post<StartCallResponse>('/start-call', {
    patient_id: patientId,
    phone_number: phoneNumber,
    client_name: clientName
  });
  return response.data;
};

// POST /end-call/:sessionId - End a call session
export const endCall = async (sessionId: string): Promise<void> => {
  await api.post(`/end-call/${sessionId}`);
};

// POST /auth/logout - Logout user
export const logout = async (): Promise<void> => {
  await api.post('/auth/logout');
};

// Metrics Types
export interface MetricsSummary {
  period: string;
  total_calls: number;
  completed: number;
  failed: number;
  voicemail: number;
  in_progress: number;
  success_rate: number;
  avg_duration_seconds: number;
  total_cost_usd: number;
  period_start: string;
  period_end: string;
}

// GET /metrics/summary - Fetch metrics summary
export const getMetricsSummary = async (period: 'day' | 'week' | 'month' = 'day'): Promise<MetricsSummary> => {
  const response = await api.get<MetricsSummary>('/metrics/summary', { params: { period } });
  return response.data;
};

// GET /metrics/breakdown/status - Get calls breakdown by status
export const getMetricsStatusBreakdown = async (period: 'day' | 'week' | 'month' = 'day') => {
  const response = await api.get('/metrics/breakdown/status', { params: { period } });
  return response.data;
};

// GET /metrics/daily - Get daily metrics for chart
export const getMetricsDaily = async (days: number = 7) => {
  const response = await api.get('/metrics/daily', { params: { days } });
  return response.data;
};

// MFA Types
export interface MFASetupResponse {
  secret: string;
  provisioning_uri: string;
  qr_code: string;
}

export interface MFAVerifyResponse {
  success: boolean;
  backup_codes: string[];
}

export interface MFAStatusResponse {
  enabled: boolean;
  backup_codes_remaining: number;
}

// GET /auth/mfa/status - Get MFA status
export const getMFAStatus = async (): Promise<MFAStatusResponse> => {
  const response = await api.get<MFAStatusResponse>('/auth/mfa/status');
  return response.data;
};

// POST /auth/mfa/setup - Start MFA setup
export const setupMFA = async (): Promise<MFASetupResponse> => {
  const response = await api.post<MFASetupResponse>('/auth/mfa/setup');
  return response.data;
};

// POST /auth/mfa/verify - Verify code and enable MFA
export const verifyMFA = async (code: string): Promise<MFAVerifyResponse> => {
  const response = await api.post<MFAVerifyResponse>('/auth/mfa/verify', { code });
  return response.data;
};

// POST /auth/mfa/disable - Disable MFA
export const disableMFA = async (password: string): Promise<{ success: boolean }> => {
  const response = await api.post('/auth/mfa/disable', { password });
  return response.data;
};

// POST /auth/mfa/backup-codes - Regenerate backup codes
export const regenerateBackupCodes = async (): Promise<{ success: boolean; backup_codes: string[] }> => {
  const response = await api.post('/auth/mfa/backup-codes');
  return response.data;
};

// POST /auth/exchange-token - Exchange handoff token for JWT (marketing site callback)
export const exchangeToken = async (
  token: string,
  organizationSlug: string
): Promise<AuthResponse> => {
  const response = await api.post<AuthResponse>('/auth/exchange-token', {
    token,
    organization_slug: organizationSlug
  });
  return response.data;
};

// =============================================================================
// Admin API Types and Functions (Super Admin Only)
// =============================================================================

export interface AdminDashboard {
  calls_today: number;
  success_rate: number;
  cost_today_usd: number;
  recent_failures: Array<{
    session_id: string;
    organization_name: string;
    workflow: string;
    created_at: string;
    error: string;
  }>;
}

export interface AdminCall {
  session_id: string;
  organization_id: string;
  organization_name: string;
  workflow: string;
  status: string;
  caller_phone?: string;
  called_phone?: string;
  total_cost_usd?: number;
  created_at: string;
  completed_at?: string;
}

export interface AdminCallsResponse {
  calls: AdminCall[];
  total_count: number;
  total_pages: number;
  page: number;
  page_size: number;
}

export interface CostBreakdown {
  service: string;
  usage: string;
  rate: string;
  formula: string;
  cost_usd: number;
}

export interface AdminCallDetail {
  session_id: string;
  organization_id: string;
  organization_name: string;
  workflow: string;
  status: string;
  caller_phone?: string;
  called_phone?: string;
  patient_id?: string;
  identity_verified?: boolean;
  caller_name?: string;
  call_type?: string;
  call_reason?: string;
  routed_to?: string;
  total_cost_usd?: number;
  costs_breakdown: CostBreakdown[];
  call_transcript?: {
    messages: Array<{
      role: string;
      content: string;
      timestamp?: string;
    }>;
    message_count: number;
  };
  error_message?: string;
  langfuse_url: string;
  created_at: string;
  completed_at?: string;
  updated_at?: string;
}

export interface PeriodCost {
  cost_usd: number;
  call_count: number;
  total_minutes: number;
}

export interface ComponentCost {
  component: string;
  cost_usd: number;
}

export interface LLMModelCost {
  model: string;
  cost_usd: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface WorkflowCost {
  workflow: string;
  cost_usd: number;
  call_count: number;
  total_minutes: number;
}

export interface OrgCost {
  organization_id: string;
  organization_name: string;
  cost_usd: number;
  call_count: number;
  total_minutes: number;
}

export interface TTSCredits {
  used: number;
  plan_limit: number;
  remaining: number;
  overage: number;
  pct_used: number;
}

export interface AdminCosts {
  today: PeriodCost;
  wtd: PeriodCost;
  mtd: PeriodCost;
  by_component: ComponentCost[];
  by_llm_model?: LLMModelCost[];
  by_workflow: WorkflowCost[];
  by_organization: OrgCost[];
  tts_credits?: TTSCredits;
}

// GET /admin/dashboard - Admin dashboard metrics
export const getAdminDashboard = async (): Promise<AdminDashboard> => {
  const response = await api.get<AdminDashboard>('/admin/dashboard');
  return response.data;
};

// GET /admin/calls - Paginated calls list with filters
export const getAdminCalls = async (params: {
  page?: number;
  page_size?: number;
  organization_id?: string;
  status?: string;
  date_from?: string;
  date_to?: string;
  search?: string;
} = {}): Promise<AdminCallsResponse> => {
  const response = await api.get<AdminCallsResponse>('/admin/calls', { params });
  return response.data;
};

// GET /admin/calls/:session_id - Single call detail
export const getAdminCallDetail = async (sessionId: string): Promise<AdminCallDetail> => {
  const response = await api.get<AdminCallDetail>(`/admin/calls/${sessionId}`);
  return response.data;
};

// GET /admin/costs - Cost summary with breakdowns
export const getAdminCosts = async (): Promise<AdminCosts> => {
  const response = await api.get<AdminCosts>('/admin/costs');
  return response.data;
};

// GET /admin/rates - Get current rates for transparency
export const getAdminRates = async (): Promise<Record<string, unknown>> => {
  const response = await api.get<Record<string, unknown>>('/admin/rates');
  return response.data;
};

// GET /admin/export/costs - Export costs to Excel (returns blob and filename)
export const exportCosts = async (): Promise<{ blob: Blob; filename: string }> => {
  const response = await api.get('/admin/export/costs', {
    responseType: 'blob',
  });

  const filename =
    response.headers['content-disposition']?.split('filename=')[1] || 'optimalbot_financials.xlsx';

  return { blob: new Blob([response.data]), filename };
};

// =============================================================================
// Cost Estimator Types and Functions (Super Admin Only)
// =============================================================================

export interface ComponentRate {
  per_minute: number;
  monthly_cost: number;
}

export interface CostEstimateTier {
  llm: ComponentRate;
  tts: ComponentRate;
  stt: ComponentRate;
  telephony: ComponentRate;
  hosting: ComponentRate;
  recording: ComponentRate;
  transfer: ComponentRate;
  total: number;
}

export interface CostEstimate {
  monthly_minutes: number;
  p90: CostEstimateTier;
  avg: CostEstimateTier;
  session_count: number;
}

// GET /admin/estimate - Estimate monthly cost based on per-minute benchmarks
export const getCostEstimate = async (minutes: number): Promise<CostEstimate> => {
  const response = await api.get<CostEstimate>('/admin/estimate', { params: { minutes } });
  return response.data;
};

// =============================================================================
// Onboarding API Types and Functions (Super Admin Only)
// =============================================================================

export interface OnboardingUploadResponse {
  status: string;
  org: string;
  workflow: string;
  uploaded_files: string[];
  path: string;
}

export interface OnboardingPhase {
  count?: number;
  approved?: number;
  files?: string[];
  complete: boolean;
}

export interface OnboardingStatus {
  org: string;
  workflow: string;
  path: string;
  phases: {
    recordings: OnboardingPhase;
    transcripts: OnboardingPhase;
    sample_conversations: OnboardingPhase;
    flow_design: OnboardingPhase;
    code_generation: OnboardingPhase;
  };
}

export interface TranscribeResult {
  file: string;
  status: 'success' | 'error';
  output?: string;
  error?: string;
}

export interface TranscribeResponse {
  status: string;
  org: string;
  workflow: string;
  total_files: number;
  success_count: number;
  error_count: number;
  results: TranscribeResult[];
}

// POST /admin/onboarding/upload - Upload recordings
export const uploadRecordings = async (
  org: string,
  workflow: string,
  files: File[]
): Promise<OnboardingUploadResponse> => {
  const formData = new FormData();
  formData.append('org', org);
  formData.append('workflow', workflow);
  files.forEach((file) => {
    formData.append('files', file);
  });

  const response = await api.post<OnboardingUploadResponse>('/admin/onboarding/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return response.data;
};

// GET /admin/onboarding/status/{org}/{workflow} - Get onboarding status
export const getOnboardingStatus = async (
  org: string,
  workflow: string
): Promise<OnboardingStatus> => {
  const response = await api.get<OnboardingStatus>(`/admin/onboarding/status/${org}/${workflow}`);
  return response.data;
};

// POST /admin/onboarding/transcribe/{org}/{workflow} - Trigger transcription
export const transcribeRecordings = async (
  org: string,
  workflow: string
): Promise<TranscribeResponse> => {
  const response = await api.post<TranscribeResponse>(
    `/admin/onboarding/transcribe/${org}/${workflow}`
  );
  return response.data;
};

// =============================================================================
// Onboarding Conversation Types and Functions (Super Admin Only)
// =============================================================================

export interface ConversationUtterance {
  role: string;
  text: string;
}

export interface ConversationMetadata {
  call_type?: string;
  insurance_company?: string;
  practice_name?: string;
  outcome?: string;
  [key: string]: string | undefined;
}

export interface OnboardingConversation {
  id: string;
  organization_id: string;
  workflow: string;
  source_filename: string;
  assemblyai_id?: string;
  roles: Record<string, string>;
  conversation: ConversationUtterance[];
  metadata?: ConversationMetadata;
  status: 'cleaned' | 'approved';
  approved_by?: string;
  approved_at?: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationCreateData {
  organization_id: string;
  workflow: string;
  source_filename: string;
  assemblyai_id?: string;
  roles: Record<string, string>;
  conversation: ConversationUtterance[];
  metadata?: ConversationMetadata;
}

export interface ConversationUpdateData {
  roles?: Record<string, string>;
  conversation?: ConversationUtterance[];
  metadata?: ConversationMetadata;
}

// GET /admin/onboarding/conversations/{org}/{workflow} - List conversations
export const getOnboardingConversations = async (
  org: string,
  workflow: string
): Promise<OnboardingConversation[]> => {
  const response = await api.get<{ conversations: OnboardingConversation[] }>(
    `/admin/onboarding/conversations/${org}/${workflow}`
  );
  return response.data.conversations;
};

// GET /admin/onboarding/conversations/detail/{id} - Get single conversation
export const getOnboardingConversation = async (
  conversationId: string
): Promise<OnboardingConversation> => {
  const response = await api.get<{ conversation: OnboardingConversation }>(
    `/admin/onboarding/conversations/detail/${conversationId}`
  );
  return response.data.conversation;
};

// POST /admin/onboarding/conversations - Create conversation
export const createOnboardingConversation = async (
  data: ConversationCreateData
): Promise<{ status: string; conversation_id: string }> => {
  const response = await api.post<{ status: string; conversation_id: string }>(
    '/admin/onboarding/conversations',
    data
  );
  return response.data;
};

// PUT /admin/onboarding/conversations/{id} - Update conversation
export const updateOnboardingConversation = async (
  conversationId: string,
  data: ConversationUpdateData
): Promise<{ status: string }> => {
  const response = await api.put<{ status: string }>(
    `/admin/onboarding/conversations/${conversationId}`,
    data
  );
  return response.data;
};

// POST /admin/onboarding/conversations/{id}/approve - Approve conversation
export const approveOnboardingConversation = async (
  conversationId: string
): Promise<{ status: string }> => {
  const response = await api.post<{ status: string }>(
    `/admin/onboarding/conversations/${conversationId}/approve`
  );
  return response.data;
};

// DELETE /admin/onboarding/conversations/{id} - Delete conversation
export const deleteOnboardingConversation = async (
  conversationId: string
): Promise<{ status: string }> => {
  const response = await api.delete<{ status: string }>(
    `/admin/onboarding/conversations/${conversationId}`
  );
  return response.data;
};

export default api;
