import { useState, useEffect } from 'react';
import { Session, Patient, TranscriptMessage } from '@/types';
import { getPatient } from '@/api';
import { formatDatetime } from '@/lib/utils';
import { TranscriptViewer } from './TranscriptViewer';
import { Badge } from '@/components/ui/badge';
import { CheckCircle, XCircle } from 'lucide-react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';

interface WorkflowField {
  key: string;
  label: string;
}

interface SessionDetailSheetProps {
  session: Session | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowFields?: WorkflowField[];
}

// Format phone number for display
function formatPhone(phone?: string): string {
  if (!phone) return '-';
  const cleaned = phone.replace(/\D/g, '');
  if (cleaned.length === 11 && cleaned.startsWith('1')) {
    return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
  }
  if (cleaned.length === 10) {
    return `(${cleaned.slice(0, 3)}) ${cleaned.slice(3, 6)}-${cleaned.slice(6)}`;
  }
  return phone;
}

// Format duration between two timestamps
function formatDuration(startedAt: string, completedAt?: string): string {
  if (!completedAt) return '-';
  const start = new Date(startedAt).getTime();
  const end = new Date(completedAt).getTime();
  const seconds = Math.floor((end - start) / 1000);
  if (seconds < 0) return '-';
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remainingSeconds}s` : `${remainingSeconds}s`;
}

// Get badge variant based on session status
function getStatusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'completed':
      return 'default';
    case 'running':
    case 'starting':
      return 'secondary';
    case 'failed':
      return 'destructive';
    case 'transferred':
      return 'outline';
    default:
      return 'outline';
  }
}

// Get display text for session status
function getStatusDisplay(status: string): string {
  switch (status) {
    case 'completed':
      return 'Completed';
    case 'running':
      return 'In Progress';
    case 'starting':
      return 'Starting';
    case 'failed':
      return 'Failed';
    case 'transferred':
      return 'Transferred';
    default:
      return status;
  }
}

// Detail row component
function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex py-1.5 border-b last:border-b-0">
      <div className="font-semibold text-muted-foreground w-36 shrink-0 text-sm">
        {label}:
      </div>
      <div className="flex-1 text-foreground text-sm">{value}</div>
    </div>
  );
}

export function SessionDetailSheet({
  session,
  open,
  onOpenChange,
  workflowFields = [],
}: SessionDetailSheetProps) {
  const [patient, setPatient] = useState<Patient | null>(null);
  const [patientLoading, setPatientLoading] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);

  // Load patient data when session has patient_id
  useEffect(() => {
    if (!session?.patient_id) {
      setPatient(null);
      return;
    }

    setPatientLoading(true);
    getPatient(session.patient_id)
      .then(setPatient)
      .catch(() => setPatient(null))
      .finally(() => setPatientLoading(false));
  }, [session?.patient_id]);

  // Parse transcript from session
  useEffect(() => {
    if (!session?.call_transcript) {
      setTranscript([]);
      return;
    }

    try {
      const data = session.call_transcript;
      const messages = data.messages || [];
      setTranscript(Array.isArray(messages) ? messages : []);
    } catch {
      setTranscript([]);
    }
  }, [session?.call_transcript]);

  if (!session) return null;

  const callerDisplay = session.caller_name || formatPhone(session.caller_phone) || 'Unknown Caller';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
        <SheetHeader>
          <div className="flex items-center gap-3">
            <SheetTitle>{callerDisplay}</SheetTitle>
            <Badge variant={getStatusVariant(session.status)}>
              {getStatusDisplay(session.status)}
            </Badge>
          </div>
          <SheetDescription>
            View call details and transcript
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-4">
          {/* Call Information Card */}
          <div className="bg-card rounded-lg border p-4 space-y-3">
            <h3 className="text-lg font-semibold text-primary mb-3">
              Call Information
            </h3>
            <div className="space-y-0">
              <DetailRow
                label="Status"
                value={
                  <Badge variant={getStatusVariant(session.status)}>
                    {getStatusDisplay(session.status)}
                  </Badge>
                }
              />
              <DetailRow
                label="Caller Phone"
                value={formatPhone(session.caller_phone)}
              />
              <DetailRow
                label="Verified"
                value={
                  session.identity_verified ? (
                    <span className="flex items-center gap-1 text-green-600">
                      <CheckCircle className="h-4 w-4" />
                      Yes
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <XCircle className="h-4 w-4" />
                      No
                    </span>
                  )
                }
              />
              {session.call_reason && (
                <DetailRow label="Call Reason" value={session.call_reason} />
              )}
              {session.routed_to && (
                <DetailRow label="Outcome" value={session.routed_to} />
              )}
              <DetailRow
                label="Started"
                value={formatDatetime(session.created_at)}
              />
              {session.completed_at && (
                <DetailRow
                  label="Completed"
                  value={formatDatetime(session.completed_at)}
                />
              )}
              <DetailRow
                label="Duration"
                value={formatDuration(session.created_at, session.completed_at)}
              />
            </div>
          </div>

          {/* Patient Information Card (only if verified) */}
          {session.identity_verified && session.patient_id && (
            <div className="bg-card rounded-lg border p-4 space-y-3">
              <h3 className="text-lg font-semibold text-primary mb-3">
                Patient Information
              </h3>
              {patientLoading ? (
                <p className="text-muted-foreground text-sm">Loading patient data...</p>
              ) : patient ? (
                <div className="space-y-0">
                  <DetailRow
                    label="Name"
                    value={patient.patient_name || `${patient.first_name || ''} ${patient.last_name || ''}`.trim() || '-'}
                  />
                  {patient.date_of_birth && (
                    <DetailRow label="Date of Birth" value={patient.date_of_birth} />
                  )}
                  {patient.phone_number && (
                    <DetailRow label="Phone" value={formatPhone(patient.phone_number)} />
                  )}
                  {/* Workflow-specific fields */}
                  {workflowFields.map(field => {
                    const value = patient[field.key];
                    if (value === undefined || value === null || value === '') return null;
                    return (
                      <DetailRow
                        key={field.key}
                        label={field.label}
                        value={String(value)}
                      />
                    );
                  })}
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">Patient data not available</p>
              )}
            </div>
          )}

          {/* Call Transcript Card */}
          {transcript.length > 0 ? (
            <div className="bg-card rounded-lg border p-4">
              <h3 className="text-lg font-semibold text-primary mb-3">
                Call Transcript
              </h3>
              <TranscriptViewer messages={transcript} callerLabel="Patient" />
            </div>
          ) : session.status === 'completed' ? (
            <div className="bg-card rounded-lg border p-4 text-center">
              <h3 className="text-lg font-semibold text-primary mb-3">
                Call Transcript
              </h3>
              <p className="text-muted-foreground">No transcript available for this call.</p>
            </div>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
