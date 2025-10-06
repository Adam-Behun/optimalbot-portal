import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { addPatient } from '../api';
import { AddPatientFormData } from '../types';

const AddPatientForm: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState<AddPatientFormData>({
    patient_name: '',
    date_of_birth: '',
    insurance_member_id: '',
    insurance_company_name: '',
    facility_name: '',
    cpt_code: '',
    provider_npi: '',
    appointment_time: ''
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const response = await addPatient(formData);
      alert(`Patient ${response.patient_name} added successfully!`);
      
      // Reset form
      setFormData({
        patient_name: '',
        date_of_birth: '',
        insurance_member_id: '',
        insurance_company_name: '',
        facility_name: '',
        cpt_code: '',
        provider_npi: '',
        appointment_time: ''
      });
      
      // Navigate to patient list
      navigate('/');
    } catch (err) {
      console.error('Error adding patient:', err);
      setError('Failed to add patient. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '8px 12px',
    border: '1px solid #dee2e6',
    borderRadius: '4px',
    fontSize: '14px',
    boxSizing: 'border-box'
  };

  const labelStyle: React.CSSProperties = {
    display: 'block',
    marginBottom: '6px',
    fontWeight: 500,
    fontSize: '14px',
    color: '#333'
  };

  const fieldStyle: React.CSSProperties = {
    marginBottom: '16px'
  };

  return (
    <div style={{ padding: '20px', maxWidth: '600px', margin: '0 auto' }}>
      <h2 style={{ marginBottom: '24px' }}>Add New Patient</h2>

      {error && (
        <div style={{
          padding: '12px',
          backgroundColor: '#f8d7da',
          color: '#721c24',
          borderRadius: '4px',
          marginBottom: '20px'
        }}>
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} style={{
        backgroundColor: 'white',
        padding: '24px',
        borderRadius: '8px',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
      }}>
        {/* Patient Name */}
        <div style={fieldStyle}>
          <label style={labelStyle}>
            Patient Name <span style={{ color: 'red' }}>*</span>
          </label>
          <input
            type="text"
            name="patient_name"
            value={formData.patient_name}
            onChange={handleChange}
            required
            style={inputStyle}
            placeholder="John Doe"
          />
        </div>

        {/* Date of Birth */}
        <div style={fieldStyle}>
          <label style={labelStyle}>
            Date of Birth <span style={{ color: 'red' }}>*</span>
          </label>
          <input
            type="date"
            name="date_of_birth"
            value={formData.date_of_birth}
            onChange={handleChange}
            required
            style={inputStyle}
          />
        </div>

        {/* Insurance Member ID */}
        <div style={fieldStyle}>
          <label style={labelStyle}>Insurance Member ID</label>
          <input
            type="text"
            name="insurance_member_id"
            value={formData.insurance_member_id}
            onChange={handleChange}
            style={inputStyle}
            placeholder="ABC123456789"
          />
        </div>

        {/* Insurance Company */}
        <div style={fieldStyle}>
          <label style={labelStyle}>
            Insurance Company <span style={{ color: 'red' }}>*</span>
          </label>
          <input
            type="text"
            name="insurance_company_name"
            value={formData.insurance_company_name}
            onChange={handleChange}
            required
            style={inputStyle}
            placeholder="Blue Cross Blue Shield"
          />
        </div>

        {/* Facility Name */}
        <div style={fieldStyle}>
          <label style={labelStyle}>
            Facility Name <span style={{ color: 'red' }}>*</span>
          </label>
          <input
            type="text"
            name="facility_name"
            value={formData.facility_name}
            onChange={handleChange}
            required
            style={inputStyle}
            placeholder="City Medical Center"
          />
        </div>

        {/* CPT Code */}
        <div style={fieldStyle}>
          <label style={labelStyle}>
            CPT Code <span style={{ color: 'red' }}>*</span>
          </label>
          <input
            type="text"
            name="cpt_code"
            value={formData.cpt_code}
            onChange={handleChange}
            required
            style={inputStyle}
            placeholder="99213"
          />
        </div>

        {/* Provider NPI */}
        <div style={fieldStyle}>
          <label style={labelStyle}>
            Provider NPI <span style={{ color: 'red' }}>*</span>
          </label>
          <input
            type="text"
            name="provider_npi"
            value={formData.provider_npi}
            onChange={handleChange}
            required
            style={inputStyle}
            placeholder="1234567890"
          />
        </div>

        {/* Appointment Time */}
        <div style={fieldStyle}>
          <label style={labelStyle}>Appointment Time</label>
          <input
            type="datetime-local"
            name="appointment_time"
            value={formData.appointment_time}
            onChange={handleChange}
            style={inputStyle}
          />
        </div>

        {/* Submit Button */}
        <div style={{ display: 'flex', gap: '12px', marginTop: '24px' }}>
          <button
            type="submit"
            disabled={loading}
            style={{
              padding: '10px 24px',
              backgroundColor: '#667eea',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: '16px',
              fontWeight: 500,
              opacity: loading ? 0.6 : 1
            }}
          >
            {loading ? 'Adding...' : 'Add Patient'}
          </button>
          
          <button
            type="button"
            onClick={() => navigate('/')}
            style={{
              padding: '10px 24px',
              backgroundColor: '#f8f9fa',
              color: '#333',
              border: '1px solid #dee2e6',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '16px'
            }}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
};

export default AddPatientForm;