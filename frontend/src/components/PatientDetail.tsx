import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link, useLocation } from 'react-router-dom';
import { getPatient } from '../api';
import { Patient, TranscriptMessage } from '../types';
import { Button } from './ui/button';
import { ModeToggle } from "@/components/mode-toggle";
import { ChevronDown, ChevronRight, PhoneForwarded } from 'lucide-react';
import { getOrganization } from '../lib/auth';

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

          {/* Dynamic fields from schema */}
          {(() => {
            const org = getOrganization();
            const fields = org?.patient_schema?.fields || [];
            const sortedFields = [...fields].sort((a, b) => a.display_order - b.display_order);

            return sortedFields.map(field => {
              const value = patient.custom_fields?.[field.key];
              if (value === undefined || value === null || value === '') return null;

              return (
                <DetailRow
                  key={field.key}
                  label={field.label}
                  value={value}
                />
              );
            });
          })()}

          <DetailRow
            label="Call Status"
            value={
              <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${
                patient.call_status === 'Completed'
                  ? 'bg-green-100 text-green-800'
                  : patient.call_status === 'Call Transferred'
                    ? 'bg-purple-100 text-purple-800'
                    : patient.call_status === 'In Progress'
                      ? 'bg-yellow-100 text-yellow-800'
                      : 'bg-gray-100 text-gray-800'
              }`}>
                {patient.call_status}
              </span>
            }
          />
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
                {conversationMessages.map((message, index) => {
                  // Handle transfer events
                  if (message.type === 'transfer') {
                    return (
                      <div
                        key={index}
                        className="p-3 rounded-lg bg-purple-50 border-l-4 border-purple-500"
                      >
                        <div className="flex items-center gap-2">
                          <PhoneForwarded className="h-4 w-4 text-purple-600" />
                          <span className="font-semibold text-purple-900">
                            {message.content}
                          </span>
                        </div>
                        <div className="text-xs text-purple-600 mt-1">
                          {new Date(message.timestamp).toLocaleTimeString()}
                        </div>
                      </div>
                    );
                  }

                  // Handle regular conversation messages
                  const isAgent = message.role === 'assistant';
                  return (
                    <div
                      key={index}
                      className={`flex ${isAgent ? 'justify-end' : 'justify-start'}`}
                    >
                      <div
                        className={`p-2.5 rounded-lg border-l-4 w-2/3 ${
                          isAgent
                            ? 'bg-primary border-primary text-primary-foreground'
                            : 'bg-secondary border-secondary text-secondary-foreground'
                        }`}
                      >
                        <div className="font-semibold text-sm mb-1.5">
                          {isAgent ? 'Provider Agent' : 'Insurance Representative'}
                        </div>
                        <div className="text-sm">
                          {message.content}
                        </div>
                      </div>
                    </div>
                  );
                })}
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