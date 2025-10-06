import axios from 'axios';
import {
  Patient,
  PatientsResponse,
  PatientResponse,
  AddPatientFormData,
  AddPatientResponse,
  StartCallResponse
} from './types';

const API_BASE_URL = ''; 

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
  return response.data.patient; // Note: the backend wraps it in {patient: {...}}
};

// POST /add-patient - Add a new patient
export const addPatient = async (patientData: AddPatientFormData): Promise<AddPatientResponse> => {
  const response = await api.post<AddPatientResponse>('/add-patient', patientData);
  return response.data;
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

export default api;