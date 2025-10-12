import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getPatients, startCall, deletePatient } from '../api';
import { Patient } from '../types';

const PatientList: React.FC = () => {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [startingCall, setStartingCall] = useState<string | null>(null);
  const [deletingPatient, setDeletingPatient] = useState<string | null>(null);
  const [showDeleteMenu, setShowDeleteMenu] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    loadPatients();
  }, []);

  useEffect(() => {
    const handleClickOutside = () => setShowDeleteMenu(null);
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, []);

  const loadPatients = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getPatients();
      setPatients(data);
    } catch (err) {
      setError('Failed to load patients');
      console.error('Error loading patients:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleStartCall = async (patient: Patient) => {
    if (!patient.insurance_phone) {
      alert('Insurance phone number is missing for this patient. Please update patient information.');
      return;
    }

    try {
      setStartingCall(patient.patient_id);
      await startCall(patient.patient_id, patient.insurance_phone);
      
      await loadPatients();
      
      alert(`Call started for ${patient.patient_name}`);
    } catch (err: any) {
      console.error('Error starting call:', err);
      const errorMsg = err.response?.data?.detail || 'Failed to start call. Please try again.';
      alert(errorMsg);
    } finally {
      setStartingCall(null);
    }
  };

  const handleDeleteClick = (e: React.MouseEvent, patientId: string) => {
    e.stopPropagation();
    setShowDeleteMenu(showDeleteMenu === patientId ? null : patientId);
  };

  const handleConfirmDelete = async (e: React.MouseEvent, patient: Patient) => {
    e.stopPropagation();
    
    if (!window.confirm(`Are you sure you want to delete ${patient.patient_name}?`)) {
      setShowDeleteMenu(null);
      return;
    }

    try {
      setDeletingPatient(patient.patient_id);
      await deletePatient(patient.patient_id);
      await loadPatients();
      alert(`${patient.patient_name} deleted successfully`);
    } catch (err) {
      console.error('Error deleting patient:', err);
      alert('Failed to delete patient. Please try again.');
    } finally {
      setDeletingPatient(null);
      setShowDeleteMenu(null);
    }
  };

  const handleRowClick = (patientId: string) => {
    navigate(`/patient/${patientId}`);
  };

  const getStatusBadge = (status: string) => {
    const colors: Record<string, string> = {
      'Not Started': 'bg-gray-200 text-gray-700',
      'In Progress': 'bg-yellow-200 text-yellow-800',
      'Completed': 'bg-green-200 text-green-800'
    };
    
    return (
      <span className={`px-3 py-1 rounded-full text-sm font-medium ${colors[status] || 'bg-gray-200'}`}>
        {status}
      </span>
    );
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '40px' }}>
        <p>Loading patients...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: 'red' }}>
        <p>{error}</p>
        <button onClick={loadPatients} style={{ marginTop: '10px', padding: '8px 16px' }}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ padding: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h2 style={{ margin: 0 }}>Patients ({patients.length})</h2>
        <button onClick={loadPatients} style={{ padding: '8px 16px', cursor: 'pointer' }}>
          Refresh
        </button>
      </div>

      {patients.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
          <p>No patients with pending authorization</p>
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: 'white', boxShadow: '0 2px 4px rgba(0,0,0,0.1)' }}>
          <thead>
            <tr style={{ backgroundColor: '#f8f9fa', borderBottom: '2px solid #dee2e6' }}>
              <th style={{ padding: '12px', textAlign: 'left' }}>Patient Name</th>
              <th style={{ padding: '12px', textAlign: 'left' }}>Facility</th>
              <th style={{ padding: '12px', textAlign: 'left' }}>Insurance</th>
              <th style={{ padding: '12px', textAlign: 'left' }}>Auth Status</th>
              <th style={{ padding: '12px', textAlign: 'center' }}>Call Status</th>
              <th style={{ padding: '12px', textAlign: 'center' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {patients.map((patient) => (
              <tr
                key={patient.patient_id}
                style={{
                  borderBottom: '1px solid #dee2e6',
                  cursor: 'pointer',
                  transition: 'background-color 0.2s'
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#f8f9fa')}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'white')}
                onClick={() => handleRowClick(patient.patient_id)}
              >
                <td style={{ padding: '12px', fontWeight: 600 }}>
                  {patient.patient_name}
                </td>
                <td style={{ padding: '12px' }}>
                  {patient.facility_name}
                </td>
                <td style={{ padding: '12px' }}>
                  {patient.insurance_company_name}
                </td>
                <td style={{ padding: '12px' }}>
                  {patient.prior_auth_status}
                </td>
                <td style={{ padding: '12px', textAlign: 'center' }}>
                  {getStatusBadge(patient.call_status)}
                </td>
                <td style={{ padding: '12px', textAlign: 'center' }}>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', justifyContent: 'center' }}>
                    {patient.call_status === 'Not Started' && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleStartCall(patient);
                        }}
                        disabled={startingCall === patient.patient_id}
                        style={{
                          padding: '6px 16px',
                          backgroundColor: '#667eea',
                          color: 'white',
                          border: 'none',
                          borderRadius: '4px',
                          cursor: startingCall === patient.patient_id ? 'not-allowed' : 'pointer',
                          fontSize: '14px',
                          opacity: startingCall === patient.patient_id ? 0.6 : 1
                        }}
                      >
                        {startingCall === patient.patient_id ? 'Starting...' : 'Start Call'}
                      </button>
                    )}
                    {patient.call_status === 'In Progress' && (
                      <span style={{ color: '#666', fontSize: '14px' }}>Call in progress...</span>
                    )}
                    {patient.call_status === 'Completed' && (
                      <span style={{ color: '#28a745', fontSize: '14px' }}>✓ Completed</span>
                    )}
                    
                    <div style={{ position: 'relative' }}>
                      <button
                        onClick={(e) => handleDeleteClick(e, patient.patient_id)}
                        disabled={deletingPatient === patient.patient_id}
                        style={{
                          padding: '6px 10px',
                          backgroundColor: 'transparent',
                          border: '1px solid #dee2e6',
                          borderRadius: '4px',
                          cursor: deletingPatient === patient.patient_id ? 'not-allowed' : 'pointer',
                          fontSize: '16px',
                          lineHeight: '1'
                        }}
                      >
                        {deletingPatient === patient.patient_id ? '...' : '⋮'}
                      </button>
                      
                      {showDeleteMenu === patient.patient_id && (
                        <div
                          style={{
                            position: 'absolute',
                            right: '0',
                            top: '100%',
                            marginTop: '4px',
                            backgroundColor: 'white',
                            border: '1px solid #dee2e6',
                            borderRadius: '4px',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                            zIndex: 1000,
                            minWidth: '150px'
                          }}
                        >
                          <button
                            onClick={(e) => handleConfirmDelete(e, patient)}
                            style={{
                              width: '100%',
                              padding: '10px 16px',
                              backgroundColor: 'transparent',
                              border: 'none',
                              textAlign: 'left',
                              cursor: 'pointer',
                              color: '#dc3545',
                              fontSize: '14px',
                              fontWeight: 500
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#f8d7da')}
                            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                          >
                            Delete Patient
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default PatientList;