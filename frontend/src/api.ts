import axios from 'axios';
import {
  Patient,
  PatientsResponse,
  AddPatientFormData,
  AddPatientResponse,
  StartCallResponse,
  BulkAddResponse,
  AuthResponse
} from './types';

// Use Vite environment variable (empty string uses proxy in dev, relative URLs in production)
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// GET /patients - Fetch all patients with pending authorization
export const getPatients = async (): Promise<Patient[]> => {
  const response = await api.get<PatientsResponse>('/patients');
  return response.data.patients;
};

// GET /patients/:id - Fetch single patient by ObjectID
export const getPatient = async (patientId: string) => {
  const response = await axios.get(`${API_BASE_URL}/patients/${patientId}`);
  return response.data.patient;
};

// POST /add-patient - Add a new patient
export const addPatient = async (patientData: AddPatientFormData): Promise<AddPatientResponse> => {
  const response = await api.post<AddPatientResponse>('/add-patient', patientData);
  return response.data;
};

// POST /add-patients-bulk - Add multiple patients
export const addPatientsBulk = async (patients: AddPatientFormData[]): Promise<BulkAddResponse> => {
  const response = await api.post<BulkAddResponse>('/add-patients-bulk', { patients });
  return response.data;
};

// DELETE /patients/:id - Delete a patient
export const deletePatient = async (patientId: string): Promise<void> => {
  await api.delete(`/patients/${patientId}`);
};

// POST /start-call - Start a call for a patient
export const startCall = async (patientId: string, phoneNumber: string): Promise<StartCallResponse> => {
  const response = await api.post<StartCallResponse>('/start-call', {
    patient_id: patientId,
    phone_number: phoneNumber
  });
  return response.data;
};

// POST /end-call/:sessionId - End a call session
export const endCall = async (sessionId: string): Promise<void> => {
  await api.post(`/end-call/${sessionId}`);
};

// POST /auth/signup - Create new user account
export const signup = async (email: string, password: string): Promise<AuthResponse> => {
  const response = await api.post<AuthResponse>('/auth/signup', { email, password });
  return response.data;
};

// POST /auth/login - Authenticate user
export const login = async (email: string, password: string): Promise<AuthResponse> => {
  const response = await api.post<AuthResponse>('/auth/login', { email, password });
  return response.data;
};

// POST /auth/logout - Logout user
export const logout = async (): Promise<void> => {
  await api.post('/auth/logout');
};

export default api;
