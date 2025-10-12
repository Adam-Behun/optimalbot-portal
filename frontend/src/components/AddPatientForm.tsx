import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { addPatient, addPatientsBulk } from '../api';
import { AddPatientFormData } from '../types';
import Papa from 'papaparse';

const AddPatientForm: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState<AddPatientFormData>({
    patient_name: '',
    date_of_birth: '',
    insurance_member_id: '',
    insurance_company_name: '',
    insurance_phone: '',
    facility_name: '',
    cpt_code: '',
    provider_npi: '',
    provider_name: '',
    appointment_time: ''
  });

  const [csvPatients, setCsvPatients] = useState<AddPatientFormData[]>([]);
  const [csvErrors, setCsvErrors] = useState<string[]>([]);
  const [uploadingBulk, setUploadingBulk] = useState(false);

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
      
      setFormData({
        patient_name: '',
        date_of_birth: '',
        insurance_member_id: '',
        insurance_company_name: '',
        insurance_phone: '',
        facility_name: '',
        cpt_code: '',
        provider_npi: '',
        provider_name: '',
        appointment_time: ''
      });
      
      navigate('/');
    } catch (err) {
      console.error('Error adding patient:', err);
      setError('Failed to add patient. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleDownloadExample = () => {
    const exampleCSV = `patient_name,date_of_birth,insurance_member_id,insurance_company_name,insurance_phone,facility_name,cpt_code,provider_npi,provider_name,appointment_time
John Doe,1990-05-15,ABC123456789,Blue Cross Blue Shield,+11234567890,City Medical Center,99213,1234567890,Dr. Jane Smith,2025-10-15T10:00
Jane Smith,1985-08-20,XYZ987654321,Aetna,+19876543210,Community Hospital,99214,0987654321,Dr. John Johnson,2025-10-16T14:30`;
    
    const blob = new Blob([exampleCSV], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'patient_upload_example.csv';
    a.click();
    window.URL.revokeObjectURL(url);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setCsvErrors([]);
    setCsvPatients([]);

    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      dynamicTyping: false,
      complete: (results) => {
        const errors: string[] = [];
        const patients: AddPatientFormData[] = [];

        const requiredColumns = [
          'patient_name',
          'date_of_birth',
          'insurance_member_id',
          'insurance_company_name',
          'insurance_phone',
          'facility_name',
          'cpt_code',
          'provider_npi',
          'provider_name',
          'appointment_time'
        ];

        // Check for missing columns
        const headers = results.meta.fields || [];
        const missingColumns = requiredColumns.filter(col => !headers.includes(col));
        
        if (missingColumns.length > 0) {
          errors.push(`Missing required columns: ${missingColumns.join(', ')}`);
          setCsvErrors(errors);
          return;
        }

        // Validate each row
        results.data.forEach((row: any, idx: number) => {
          const missingFields = requiredColumns.filter(col => !row[col] || row[col].trim() === '');
          
          if (missingFields.length > 0) {
            errors.push(`Row ${idx + 1}: Missing ${missingFields.join(', ')}`);
          } else {
            // Validate phone format
            const phonePattern = /^\+\d{10,15}$/;
            if (!phonePattern.test(row.insurance_phone)) {
              errors.push(`Row ${idx + 1}: Invalid phone format (must be +1234567890)`);
            } else {
              patients.push({
                patient_name: row.patient_name.trim(),
                date_of_birth: row.date_of_birth.trim(),
                insurance_member_id: row.insurance_member_id.trim(),
                insurance_company_name: row.insurance_company_name.trim(),
                insurance_phone: row.insurance_phone.trim(),
                facility_name: row.facility_name.trim(),
                cpt_code: row.cpt_code.trim(),
                provider_npi: row.provider_npi.trim(),
                provider_name: row.provider_name.trim(),
                appointment_time: row.appointment_time.trim()
              });
            }
          }
        });

        setCsvErrors(errors);
        setCsvPatients(patients);
      },
      error: (error) => {
        setCsvErrors([`Failed to parse CSV: ${error.message}`]);
      }
    });

    e.target.value = '';
  };

  const handleBulkUpload = async () => {
    if (csvPatients.length === 0) return;

    setUploadingBulk(true);
    setError(null);

    try {
      const response = await addPatientsBulk(csvPatients);
      
      if (response.failed_count > 0) {
        const errorMsg = `Added ${response.success_count} patients. ${response.failed_count} failed.`;
        alert(errorMsg);
      } else {
        alert(`Successfully added ${response.success_count} patients!`);
      }
      
      navigate('/');
    } catch (err) {
      console.error('Error uploading patients:', err);
      setError('Failed to upload patients. Please try again.');
    } finally {
      setUploadingBulk(false);
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
    <div style={{ padding: '20px', maxWidth: '900px', margin: '0 auto' }}>
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

      {/* CSV Upload Section */}
      <div style={{
        backgroundColor: 'white',
        padding: '24px',
        borderRadius: '8px',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
        marginBottom: '24px'
      }}>
        <h3 style={{ marginTop: 0, marginBottom: '16px' }}>Bulk Upload (CSV)</h3>
        
        <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
          <label style={{
            padding: '10px 24px',
            backgroundColor: '#667eea',
            color: 'white',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '16px',
            fontWeight: 500
          }}>
            Upload CSV
            <input
              type="file"
              accept=".csv"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
            />
          </label>
          
          <button
            type="button"
            onClick={handleDownloadExample}
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
            Download Example CSV
          </button>
        </div>

        {csvErrors.length > 0 && (
          <div style={{
            padding: '12px',
            backgroundColor: '#f8d7da',
            color: '#721c24',
            borderRadius: '4px',
            marginBottom: '16px'
          }}>
            <strong>Validation Errors:</strong>
            <ul style={{ margin: '8px 0 0 0', paddingLeft: '20px' }}>
              {csvErrors.map((err, idx) => (
                <li key={idx}>{err}</li>
              ))}
            </ul>
          </div>
        )}

        {csvPatients.length > 0 && (
          <>
            <div style={{
              padding: '12px',
              backgroundColor: '#d4edda',
              color: '#155724',
              borderRadius: '4px',
              marginBottom: '16px'
            }}>
              <strong>✓ {csvPatients.length} patients ready to upload</strong>
            </div>

            <div style={{ maxHeight: '300px', overflowY: 'auto', marginBottom: '16px' }}>
              <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
                <thead style={{ position: 'sticky', top: 0, backgroundColor: '#f8f9fa' }}>
                  <tr>
                    <th style={{ padding: '8px', textAlign: 'left', borderBottom: '2px solid #dee2e6' }}>Name</th>
                    <th style={{ padding: '8px', textAlign: 'left', borderBottom: '2px solid #dee2e6' }}>DOB</th>
                    <th style={{ padding: '8px', textAlign: 'left', borderBottom: '2px solid #dee2e6' }}>Insurance</th>
                    <th style={{ padding: '8px', textAlign: 'left', borderBottom: '2px solid #dee2e6' }}>Phone</th>
                    <th style={{ padding: '8px', textAlign: 'left', borderBottom: '2px solid #dee2e6' }}>Facility</th>
                  </tr>
                </thead>
                <tbody>
                  {csvPatients.map((patient, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid #f0f0f0' }}>
                      <td style={{ padding: '8px' }}>{patient.patient_name}</td>
                      <td style={{ padding: '8px' }}>{patient.date_of_birth}</td>
                      <td style={{ padding: '8px' }}>{patient.insurance_company_name}</td>
                      <td style={{ padding: '8px' }}>{patient.insurance_phone}</td>
                      <td style={{ padding: '8px' }}>{patient.facility_name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <button
              onClick={handleBulkUpload}
              disabled={uploadingBulk}
              style={{
                padding: '10px 24px',
                backgroundColor: '#28a745',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: uploadingBulk ? 'not-allowed' : 'pointer',
                fontSize: '16px',
                fontWeight: 500,
                opacity: uploadingBulk ? 0.6 : 1
              }}
            >
              {uploadingBulk ? 'Adding Patients...' : `Add ${csvPatients.length} Patients`}
            </button>
          </>
        )}
      </div>

      {/* Single Patient Form */}
      <div style={{
        backgroundColor: 'white',
        padding: '24px',
        borderRadius: '8px',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
      }}>
        <h3 style={{ marginTop: 0, marginBottom: '20px' }}>Or Add Single Patient</h3>
        
        <form onSubmit={handleSubmit}>
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

          <div style={fieldStyle}>
            <label style={labelStyle}>
              Insurance Member ID <span style={{ color: 'red' }}>*</span>
            </label>
            <input
              type="text"
              name="insurance_member_id"
              value={formData.insurance_member_id}
              onChange={handleChange}
              required
              style={inputStyle}
              placeholder="ABC123456789"
            />
          </div>

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

          <div style={fieldStyle}>
            <label style={labelStyle}>
              Insurance Phone <span style={{ color: 'red' }}>*</span>
            </label>
            <input
              type="tel"
              name="insurance_phone"
              value={formData.insurance_phone}
              onChange={handleChange}
              required
              pattern="\+[0-9]{10,15}"
              style={inputStyle}
              placeholder="+11234567890"
            />
          </div>

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

          <div style={fieldStyle}>
            <label style={labelStyle}>
              Provider Name <span style={{ color: 'red' }}>*</span>
            </label>
            <input
              type="text"
              name="provider_name"
              value={formData.provider_name}
              onChange={handleChange}
              required
              style={inputStyle}
              placeholder="Dr. Jane Smith"
            />
          </div>

          <div style={fieldStyle}>
            <label style={labelStyle}>
              Appointment Time <span style={{ color: 'red' }}>*</span>
            </label>
            <input
              type="datetime-local"
              name="appointment_time"
              value={formData.appointment_time}
              onChange={handleChange}
              required
              style={inputStyle}
            />
          </div>

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
    </div>
  );
};

export default AddPatientForm;