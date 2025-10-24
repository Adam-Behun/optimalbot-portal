import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link, useLocation } from 'react-router-dom';
import { getPatient } from '../api';
import { Patient, TranscriptMessage } from '../types';
import { Button } from './ui/button';
import { ModeToggle } from "@/components/mode-toggle";

interface PatientDetailProps {
  patientId?: string;
  hideBackButton?: boolean;
  onClose?: () => void;
}

const PatientDetail: React.FC<PatientDetailProps> = ({
  patientId: propPatientId,
  hideBackButton = false,
  onClose
}) => {
  const params = useParams();
  const routePatientId = params.patientId;
  const patientId = propPatientId || routePatientId;
  const navigate = useNavigate();
  const location = useLocation();

  const [patient, setPatient] = useState<Patient | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);

  const isActive = (path: string) => location.pathname === path;

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
      <div className="text-center py-10">
        <p className="text-muted-foreground">Loading patient details...</p>
      </div>
    );
  }

  if (error || !patient) {
    return (
      <div className="text-center py-10">
        <p className="text-destructive mb-4">{error || 'Patient not found'}</p>
        {!hideBackButton && (
          <Button onClick={() => navigate('/')} variant="outline">
            Back to List
          </Button>
        )}
      </div>
    );
  }

  // For standalone pages, wrap in same container as PatientList
  const content = (
    <div className="space-y-4">
      {/* Navigation - only show on standalone page (not in sheet) */}
      {!hideBackButton && !onClose && (
        <div className="flex items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-6">
            <Link
              to="/"
              className={`px-4 py-2 inline-block transition-all border-b-2 ${
                isActive('/')
                  ? 'text-primary border-primary font-semibold'
                  : 'text-muted-foreground border-transparent hover:text-foreground'
              }`}
            >
              Patients
            </Link>
            <Link
              to="/add-patient"
              className={`px-4 py-2 inline-block transition-all border-b-2 ${
                isActive('/add-patient')
                  ? 'text-primary border-primary font-semibold'
                  : 'text-muted-foreground border-transparent hover:text-foreground'
              }`}
            >
              Add Patient
            </Link>
          </div>
          <ModeToggle />
        </div>
      )}

      {/* Header with Back Button */}
      {!hideBackButton && (
        <div className="flex items-center gap-4">
          <Button
            onClick={() => onClose ? onClose() : navigate('/')}
            variant="outline"
            size="sm"
          >
            ← Back to List
          </Button>
          <h2 className="text-2xl font-semibold">Patient Details</h2>
        </div>
      )}

      {/* Patient Information Card */}
      <div className="bg-card rounded-lg border p-4 space-y-3">
        <h3 className="text-lg font-semibold text-primary mb-3">
          Patient Information
        </h3>

        <div className="space-y-0">
          <DetailRow label="Patient Name" value={patient.patient_name} />
          <DetailRow label="Date of Birth" value={patient.date_of_birth} />
          <DetailRow label="Facility" value={patient.facility_name} />
          <DetailRow label="Insurance Company" value={patient.insurance_company_name} />
          <DetailRow label="Insurance Member ID" value={patient.insurance_member_id} />
          <DetailRow label="Insurance Phone" value={patient.insurance_phone} />
          <DetailRow label="CPT Code" value={patient.cpt_code} />
          <DetailRow label="Provider NPI" value={patient.provider_npi} />
          <DetailRow label="Provider Name" value={patient.provider_name} />
          <DetailRow
            label="Appointment Time"
            value={new Date(patient.appointment_time).toLocaleString()}
          />
          <DetailRow label="Prior Auth Status" value={patient.prior_auth_status} />
          <DetailRow
            label="Call Status"
            value={
              <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${
                patient.call_status === 'Completed'
                  ? 'bg-green-100 text-green-800'
                  : patient.call_status === 'In Progress'
                    ? 'bg-yellow-100 text-yellow-800'
                    : 'bg-gray-100 text-gray-800'
              }`}>
                {patient.call_status}
              </span>
            }
          />
          {patient.reference_number && (
            <DetailRow label="Reference Number" value={patient.reference_number} />
          )}
        </div>
      </div>

      {/* Call Transcript Section */}
      {transcript.length > 0 && (
        <div className="bg-card rounded-lg border p-4">
          <h3 className="text-lg font-semibold text-primary mb-3">
            Call Transcript
          </h3>

          <div className="space-y-2 max-h-[600px] overflow-y-auto">
            {transcript.map((message, index) => (
              <div
                key={index}
                className={`p-2.5 rounded-lg border-l-4 ${
                  message.role === 'assistant'
                    ? 'bg-blue-50 border-blue-500'
                    : 'bg-gray-50 border-gray-500'
                }`}
              >
                <div className="font-semibold text-sm mb-1.5">
                  {message.role === 'assistant' ? 'AI Agent' : 'Insurance Rep'}
                </div>
                <div className="text-sm text-muted-foreground">
                  {message.content}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {transcript.length === 0 && patient.call_status === 'Completed' && (
        <div className="bg-card rounded-lg border p-4 text-center">
          <p className="text-muted-foreground">No transcript available for this call.</p>
        </div>
      )}
    </div>
  );

  // In sheet: return content directly. Standalone page: wrap in container
  if (onClose) {
    return content;
  }

  return (
    <div className="max-w-5xl mx-auto py-8 px-4 space-y-6">
      {content}
    </div>
  );
};

interface DetailRowProps {
  label: string;
  value: React.ReactNode;
}

function DetailRow({ label, value }: DetailRowProps) {
  return (
    <div className="flex py-1.5 border-b last:border-b-0">
      <div className="font-semibold text-muted-foreground w-48">
        {label}:
      </div>
      <div className="flex-1 text-foreground">{value}</div>
    </div>
  );
}

export default PatientDetail;