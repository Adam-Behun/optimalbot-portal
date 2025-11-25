import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { TranscriptViewer } from '../shared/TranscriptViewer';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient, SchemaField, TranscriptMessage } from '@/types';
import { getPatient, startCall } from '@/api';
import { Phone, Edit, Loader2 } from 'lucide-react';

// Format value based on field type
function formatValue(value: unknown, field: SchemaField): string {
  if (value === null || value === undefined || value === '') return '-';
  switch (field.type) {
    case 'date':
      return new Date(value as string).toLocaleDateString();
    case 'datetime':
      return new Date(value as string).toLocaleString();
    default:
      return String(value);
  }
}

// Detail row component matching old design
function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex py-1.5 border-b last:border-b-0">
      <div className="font-semibold text-muted-foreground w-48">
        {label}:
      </div>
      <div className="flex-1 text-foreground">{value}</div>
    </div>
  );
}

export function PriorAuthPatientDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('prior_auth');

  const [patient, setPatient] = useState<Patient | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [callLoading, setCallLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;

    const fetchData = async () => {
      try {
        const patientData = await getPatient(id);
        setPatient(patientData);

        // Parse transcript from patient data
        if (patientData.call_transcript) {
          try {
            let parsed;
            if (typeof patientData.call_transcript === 'string') {
              parsed = JSON.parse(patientData.call_transcript);
            } else {
              parsed = patientData.call_transcript;
            }
            const messages = parsed.messages || parsed;
            setTranscript(Array.isArray(messages) ? messages : []);
          } catch (e) {
            console.error('Error parsing transcript:', e);
            setTranscript([]);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [id]);

  const handleStartCall = async () => {
    if (!patient || !id) return;
    try {
      setCallLoading(true);
      const phoneNumber = patient.insurance_phone || patient.phone;
      if (!phoneNumber) {
        setError('No phone number available');
        return;
      }
      await startCall(id, phoneNumber, 'prior_auth');
      // Refresh patient data
      const updated = await getPatient(id);
      setPatient(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setCallLoading(false);
    }
  };

  if (loading) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Patient Details">
        <div className="text-center py-10">
          <p className="text-muted-foreground">Loading patient details...</p>
        </div>
      </WorkflowLayout>
    );
  }

  if (error || !patient || !schema) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Patient Details">
        <div className="text-center py-10">
          <p className="text-destructive mb-4">{error || 'Patient not found'}</p>
          <Button onClick={() => navigate('/workflows/prior_auth/patients')} variant="outline">
            Back to List
          </Button>
        </div>
      </WorkflowLayout>
    );
  }

  // Separate computed and non-computed fields
  const allFields = [...schema.patient_schema.fields].sort((a, b) => a.display_order - b.display_order);
  const regularFields = allFields.filter(f => !f.computed);
  const computedFields = allFields.filter(f => f.computed);

  return (
    <WorkflowLayout
      workflowName="prior_auth"
      title="Patient Details"
      actions={
        <div className="flex gap-2">
          <Button
            onClick={handleStartCall}
            disabled={callLoading || patient.call_status === 'In Progress'}
          >
            {callLoading ? (
              <><Loader2 className="h-4 w-4 animate-spin mr-2" /> Starting...</>
            ) : (
              <><Phone className="h-4 w-4 mr-2" /> Start Call</>
            )}
          </Button>
          <Button
            variant="outline"
            onClick={() => navigate(`/workflows/prior_auth/patients/${id}/edit`)}
          >
            <Edit className="h-4 w-4 mr-2" /> Edit
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        {/* Patient Information Card */}
        <div className="bg-card rounded-lg border p-4 space-y-3">
          <h3 className="text-lg font-semibold text-primary mb-3">
            Patient Information
          </h3>

          <div className="space-y-0">
            {regularFields.map(field => (
              <DetailRow
                key={field.key}
                label={field.label}
                value={formatValue(patient[field.key], field)}
              />
            ))}
            <DetailRow
              label="Call Status"
              value={
                <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${
                  patient.call_status === 'Completed'
                    ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
                    : patient.call_status === 'Call Transferred'
                      ? 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200'
                      : patient.call_status === 'In Progress'
                        ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200'
                        : 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200'
                }`}>
                  {patient.call_status}
                </span>
              }
            />
          </div>
        </div>

        {/* Authorization Status Card */}
        {computedFields.length > 0 && (
          <div className="bg-card rounded-lg border p-4 space-y-3">
            <h3 className="text-lg font-semibold text-primary mb-3">
              Authorization Status
            </h3>

            <div className="space-y-0">
              {computedFields.map(field => (
                <DetailRow
                  key={field.key}
                  label={field.label}
                  value={
                    field.key === 'prior_auth_status' && patient[field.key] ? (
                      <Badge
                        variant={patient[field.key] === 'Denied' ? 'destructive' : 'secondary'}
                        className={patient[field.key] === 'Approved' ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200' : ''}
                      >
                        {patient[field.key]}
                      </Badge>
                    ) : (
                      formatValue(patient[field.key], field)
                    )
                  }
                />
              ))}
              <DetailRow
                label="Created"
                value={patient.created_at ? new Date(patient.created_at).toLocaleString() : '-'}
              />
              <DetailRow
                label="Last Updated"
                value={patient.updated_at ? new Date(patient.updated_at).toLocaleString() : '-'}
              />
            </div>
          </div>
        )}

        {/* Call Transcript Section */}
        {transcript.length > 0 ? (
          <div className="bg-card rounded-lg border p-4">
            <h3 className="text-lg font-semibold text-primary mb-3">
              Call Transcript
            </h3>
            <TranscriptViewer messages={transcript} />
          </div>
        ) : patient.call_status === 'Completed' ? (
          <div className="bg-card rounded-lg border p-4 text-center">
            <h3 className="text-lg font-semibold text-primary mb-3">
              Call Transcript
            </h3>
            <p className="text-muted-foreground">No transcript available for this call.</p>
          </div>
        ) : (
          <div className="bg-card rounded-lg border p-4 text-center">
            <h3 className="text-lg font-semibold text-primary mb-3">
              Call Transcript
            </h3>
            <p className="text-muted-foreground">No call transcript available yet</p>
          </div>
        )}
      </div>
    </WorkflowLayout>
  );
}
