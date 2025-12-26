import axios from 'axios';
import {
  Patient,
  PatientsResponse,
  AddPatientResponse,
  StartCallResponse,
  BulkAddResponse,
  AuthResponse,
  Session,
  SessionsResponse
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

export default api;
