import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link, useLocation } from 'react-router-dom';
import { getPatient } from '../api';
import { Patient, TranscriptMessage } from '../types';
import { Button } from './ui/button';
import { ModeToggle } from "@/components/mode-toggle";
import { ChevronDown, ChevronRight } from 'lucide-react';

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
  const [showIVRDetails, setShowIVRDetails] = useState(false);

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
          // Handle both object and JSON string formats
          let parsed;
          if (typeof data.call_transcript === 'string') {
            parsed = JSON.parse(data.call_transcript);
          } else {
            parsed = data.call_transcript;
          }

          // Extract messages array from transcript data structure
          // Expected format: { messages: [...], message_count: N }
          const messages = parsed.messages || parsed;
          setTranscript(Array.isArray(messages) ? messages : []);
        } catch (e) {
          console.error('Error parsing transcript:', e);
          setTranscript([]);
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
      {transcript.length > 0 && (() => {
        // Separate IVR messages from regular conversation
        const ivrMessages = transcript.filter(m => m.type === 'ivr' || m.type === 'ivr_action');
        const ivrSummary = transcript.find(m => m.type === 'ivr_summary' && m.content.includes('ompleted') || m.content.includes('ailed'));
        const conversationMessages = transcript.filter(m => m.type === 'transcript');

        return (
          <div className="bg-card rounded-lg border p-4">
            <h3 className="text-lg font-semibold text-primary mb-3">
              Call Transcript
            </h3>

            {/* IVR Navigation Summary */}
            {ivrSummary && (
              <div className="mb-4 p-3 rounded-lg bg-slate-50 border border-slate-200">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-700">
                      IVR Navigation:
                    </span>
                    <span className={`text-sm font-medium ${
                      ivrSummary.content.includes('ompleted')
                        ? 'text-green-600'
                        : 'text-red-600'
                    }`}>
                      {ivrSummary.content}
                    </span>
                  </div>
                  {ivrMessages.length > 0 && (
                    <button
                      onClick={() => setShowIVRDetails(!showIVRDetails)}
                      className="flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
                    >
                      {showIVRDetails ? (
                        <>
                          <ChevronDown size={16} />
                          Hide Details
                        </>
                      ) : (
                        <>
                          <ChevronRight size={16} />
                          Show Details
                        </>
                      )}
                    </button>
                  )}
                </div>

                {/* IVR Details Dropdown */}
                {showIVRDetails && ivrMessages.length > 0 && (
                  <div className="mt-3 space-y-2 max-h-[300px] overflow-y-auto">
                    {ivrMessages.map((message, index) => (
                      <div
                        key={index}
                        className={`p-2 rounded border-l-2 text-sm ${
                          message.type === 'ivr_action'
                            ? 'bg-amber-50 border-amber-500 font-medium'
                            : 'bg-slate-100 border-slate-400'
                        }`}
                      >
                        {message.content}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Main Conversation Transcript */}
            {conversationMessages.length > 0 && (
              <div className="space-y-2 max-h-[600px] overflow-y-auto">
                {conversationMessages.map((message, index) => (
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
            )}
          </div>
        );
      })()}

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