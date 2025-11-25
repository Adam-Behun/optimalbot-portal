import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { TranscriptViewer } from '../shared/TranscriptViewer';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient, Session, SchemaField } from '@/types';
import { getPatient, startCall } from '@/api';
import api from '@/api';
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

// Get badge variant for call status
function getStatusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'Completed':
    case 'Completed - Left VM':
      return 'default';
    case 'Failed':
      return 'destructive';
    case 'In Progress':
      return 'secondary';
    default:
      return 'outline';
  }
}

export function PriorAuthPatientDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('prior_auth');

  const [patient, setPatient] = useState<Patient | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [callLoading, setCallLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;

    const fetchData = async () => {
      try {
        const patientData = await getPatient(id);
        setPatient(patientData);

        // Try to fetch session for transcript
        try {
          const sessionRes = await api.get(`/sessions?patient_id=${id}&limit=1`);
          if (sessionRes.data.sessions?.length > 0) {
            setSession(sessionRes.data.sessions[0]);
          }
        } catch {
          // Session fetch is optional
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
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
      </WorkflowLayout>
    );
  }

  if (error || !patient || !schema) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Patient Details">
        <p className="text-red-500">{error || 'Patient not found'}</p>
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
      <div className="grid gap-6 md:grid-cols-2">
        {/* Patient Information */}
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle>Patient Information</CardTitle>
              <Badge variant={getStatusVariant(patient.call_status)}>
                {patient.call_status}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              {regularFields.map(field => (
                <div key={field.key} className="flex justify-between">
                  <dt className="text-muted-foreground">{field.label}</dt>
                  <dd className="font-medium">{formatValue(patient[field.key], field)}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>

        {/* Authorization Status */}
        <Card>
          <CardHeader>
            <CardTitle>Authorization Status</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              {computedFields.map(field => (
                <div key={field.key} className="flex justify-between">
                  <dt className="text-muted-foreground">{field.label}</dt>
                  <dd className="font-medium">
                    {field.key === 'prior_auth_status' && patient[field.key] ? (
                      <Badge
                        variant={patient[field.key] === 'Denied' ? 'destructive' : 'secondary'}
                        className={patient[field.key] === 'Approved' ? 'bg-green-100 text-green-800' : ''}
                      >
                        {patient[field.key]}
                      </Badge>
                    ) : (
                      formatValue(patient[field.key], field)
                    )}
                  </dd>
                </div>
              ))}
              <div className="flex justify-between pt-2 border-t">
                <dt className="text-muted-foreground">Created</dt>
                <dd className="font-medium">
                  {patient.created_at ? new Date(patient.created_at).toLocaleString() : '-'}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Last Updated</dt>
                <dd className="font-medium">
                  {patient.updated_at ? new Date(patient.updated_at).toLocaleString() : '-'}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      </div>

      {/* Transcript Section */}
      {session?.transcript?.messages ? (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Transcript</CardTitle>
          </CardHeader>
          <CardContent>
            <TranscriptViewer
              messages={session.transcript.messages}
              summary={session.transcript.summary}
            />
          </CardContent>
        </Card>
      ) : (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Transcript</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground">No call transcript available yet</p>
          </CardContent>
        </Card>
      )}
    </WorkflowLayout>
  );
}
