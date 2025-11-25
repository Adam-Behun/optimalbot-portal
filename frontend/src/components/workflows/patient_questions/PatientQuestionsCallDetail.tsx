import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { TranscriptViewer } from '../shared/TranscriptViewer';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient, Session, SchemaField } from '@/types';
import { getPatient } from '@/api';
import api from '@/api';
import { Loader2 } from 'lucide-react';

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

export function PatientQuestionsCallDetail() {
  const { id } = useParams<{ id: string }>();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('patient_questions');

  const [patient, setPatient] = useState<Patient | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
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

  if (loading) {
    return (
      <WorkflowLayout workflowName="patient_questions" title="Call Details">
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
      </WorkflowLayout>
    );
  }

  if (error || !patient || !schema) {
    return (
      <WorkflowLayout workflowName="patient_questions" title="Call Details">
        <p className="text-red-500">{error || 'Call record not found'}</p>
      </WorkflowLayout>
    );
  }

  // Get all fields from schema, sorted by display_order
  const allFields = [...schema.patient_schema.fields].sort((a, b) => a.display_order - b.display_order);

  return (
    <WorkflowLayout
      workflowName="patient_questions"
      title="Call Details"
    >
      <div className="grid gap-6 md:grid-cols-2">
        {/* Call Information - dynamically rendered from schema */}
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle>Call Information</CardTitle>
              <Badge variant={getStatusVariant(patient.call_status)}>
                {patient.call_status}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              {allFields.map(field => (
                <div key={field.key} className="flex justify-between">
                  <dt className="text-muted-foreground">{field.label}</dt>
                  <dd className="font-medium">{formatValue(patient[field.key], field)}</dd>
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

        {/* Session Info */}
        <Card>
          <CardHeader>
            <CardTitle>Session Info</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Session ID</dt>
                <dd className="font-medium text-sm font-mono">
                  {session?.session_id || patient.last_call_session_id || '-'}
                </dd>
              </div>
              {session && (
                <>
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">Started</dt>
                    <dd className="font-medium">
                      {session.started_at ? new Date(session.started_at).toLocaleString() : '-'}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">Ended</dt>
                    <dd className="font-medium">
                      {session.ended_at ? new Date(session.ended_at).toLocaleString() : '-'}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">Duration</dt>
                    <dd className="font-medium">
                      {session.duration_seconds
                        ? `${Math.floor(session.duration_seconds / 60)}m ${session.duration_seconds % 60}s`
                        : '-'}
                    </dd>
                  </div>
                </>
              )}
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
            <p className="text-muted-foreground">No transcript available yet</p>
          </CardContent>
        </Card>
      )}
    </WorkflowLayout>
  );
}
