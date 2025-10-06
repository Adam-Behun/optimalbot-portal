import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getPatient } from '../api';
import { Patient, TranscriptMessage } from '../types';

const PatientDetail: React.FC = () => {
  const params = useParams();
  const patientId = params.patientId;
  const navigate = useNavigate();
  
  const [patient, setPatient] = useState<Patient | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);

  useEffect(() => {
    if (patientId) {
      loadPatient(patientId);
    }
  }, [patientId]);

  const loadPatient = async (id: string) => {
    try {
      setLoading(true);
      setError(null);
      const data = await getPatient(id);
      setPatient(data);

      if (data.call_transcript) {
        try {
          const parsed = JSON.parse(data.call_transcript);
          setTranscript(parsed);
        } catch (e) {
          console.error('Error parsing transcript:', e);
        }
      }
    } catch (err) {
      setError('Failed to load patient details');
      console.error('Error loading patient:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '40px' }}>
        <p>Loading patient details...</p>
      </div>
    );
  }

  if (error || !patient) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: 'red' }}>
        <p>{error || 'Patient not found'}</p>
        <button onClick={() => navigate('/')} style={{ marginTop: '10px', padding: '8px 16px' }}>
          Back to List
        </button>
      </div>
    );
  }

  const detailRowStyle: React.CSSProperties = {
    display: 'flex',
    padding: '12px 0',
    borderBottom: '1px solid #f0f0f0'
  };

  const labelStyle: React.CSSProperties = {
    fontWeight: 600,
    width: '200px',
    color: '#666'
  };

  const valueStyle: React.CSSProperties = {
    flex: 1,
    color: '#333'
  };

  return (
    <div style={{ padding: '20px', maxWidth: '1200px', margin: '0 auto' }}>
      {/* Header with Back Button */}
      <div style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '16px' }}>
        <button
          onClick={() => navigate('/')}
          style={{
            padding: '8px 16px',
            backgroundColor: '#f8f9fa',
            border: '1px solid #dee2e6',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '14px'
          }}
        >
          ← Back to List
        </button>
        <h2 style={{ margin: 0 }}>Patient Details</h2>
      </div>

      {/* Patient Information Card */}
      <div style={{
        backgroundColor: 'white',
        borderRadius: '8px',
        padding: '24px',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
        marginBottom: '24px'
      }}>
        <h3 style={{ marginTop: 0, marginBottom: '20px', color: '#667eea' }}>
          Patient Information
        </h3>
        
        <div style={detailRowStyle}>
          <div style={labelStyle}>Patient Name:</div>
          <div style={valueStyle}>{patient.patient_name}</div>
        </div>

        <div style={detailRowStyle}>
          <div style={labelStyle}>Date of Birth:</div>
          <div style={valueStyle}>{patient.date_of_birth}</div>
        </div>

        <div style={detailRowStyle}>
          <div style={labelStyle}>Facility:</div>
          <div style={valueStyle}>{patient.facility_name}</div>
        </div>

        <div style={detailRowStyle}>
          <div style={labelStyle}>Insurance Company:</div>
          <div style={valueStyle}>{patient.insurance_company_name}</div>
        </div>

        {patient.insurance_member_id && (
          <div style={detailRowStyle}>
            <div style={labelStyle}>Member ID:</div>
            <div style={valueStyle}>{patient.insurance_member_id}</div>
          </div>
        )}

        <div style={detailRowStyle}>
          <div style={labelStyle}>CPT Code:</div>
          <div style={valueStyle}>{patient.cpt_code}</div>
        </div>

        <div style={detailRowStyle}>
          <div style={labelStyle}>Provider NPI:</div>
          <div style={valueStyle}>{patient.provider_npi}</div>
        </div>

        <div style={detailRowStyle}>
          <div style={labelStyle}>Prior Auth Status:</div>
          <div style={valueStyle}>{patient.prior_auth_status}</div>
        </div>

        <div style={detailRowStyle}>
          <div style={labelStyle}>Call Status:</div>
          <div style={valueStyle}>
            <span style={{
              padding: '4px 12px',
              borderRadius: '12px',
              fontSize: '14px',
              backgroundColor: patient.call_status === 'Completed' ? '#d4edda' : 
                             patient.call_status === 'In Progress' ? '#fff3cd' : '#e2e3e5',
              color: patient.call_status === 'Completed' ? '#155724' :
                     patient.call_status === 'In Progress' ? '#856404' : '#383d41'
            }}>
              {patient.call_status}
            </span>
          </div>
        </div>

        {patient.appointment_time && (
          <div style={detailRowStyle}>
            <div style={labelStyle}>Appointment Time:</div>
            <div style={valueStyle}>{new Date(patient.appointment_time).toLocaleString()}</div>
          </div>
        )}
      </div>

      {/* Call Transcript Section */}
      {transcript.length > 0 && (
        <div style={{
          backgroundColor: 'white',
          borderRadius: '8px',
          padding: '24px',
          boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
        }}>
          <h3 style={{ marginTop: 0, marginBottom: '20px', color: '#667eea' }}>
            Call Transcript
          </h3>
          
          <div style={{ maxHeight: '600px', overflowY: 'auto' }}>
            {transcript.map((message, index) => (
              <div
                key={index}
                style={{
                  marginBottom: '16px',
                  padding: '12px',
                  backgroundColor: message.role === 'assistant' ? '#f0f4ff' : '#f8f9fa',
                  borderRadius: '8px',
                  borderLeft: `4px solid ${message.role === 'assistant' ? '#667eea' : '#6c757d'}`
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: '4px', color: '#333' }}>
                  {message.role === 'assistant' ? 'AI Agent' : 'Insurance Rep'}
                </div>
                <div style={{ color: '#555' }}>{message.content}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {transcript.length === 0 && patient.call_status === 'Completed' && (
        <div style={{
          backgroundColor: 'white',
          borderRadius: '8px',
          padding: '24px',
          boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
          textAlign: 'center',
          color: '#666'
        }}>
          <p>No transcript available for this call.</p>
        </div>
      )}
    </div>
  );
};

export default PatientDetail;