import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { DynamicTable } from '../shared/DynamicTable';
import { TranscriptViewer } from '../shared/TranscriptViewer';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient, SchemaField, TranscriptMessage } from '@/types';
import { getPatients, getPatient, deletePatient } from '@/api';
import { formatDate, formatDatetime, formatTime } from '@/lib/utils';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

// Format value based on field type
function formatValue(value: unknown, field: SchemaField): string {
  if (value === null || value === undefined || value === '') return '-';
  const strValue = String(value);

  switch (field.type) {
    case 'date':
      return formatDate(strValue);
    case 'datetime':
      return formatDatetime(strValue);
    case 'time':
      return formatTime(strValue);
    default:
      return strValue;
  }
}

// Get call status badge styling
function getCallStatusStyle(status: string): string {
  switch (status) {
    case 'Completed':
      return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200';
    case 'In Progress':
      return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200';
    case 'Failed':
      return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200';
    case 'Transferred':
      return 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200';
    default:
      return 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200';
  }
}

// Render field value with special formatting
function renderFieldValue(value: unknown, field: SchemaField): React.ReactNode {
  if (value === null || value === undefined || value === '') return '-';

  // Special styling for call_status field
  if (field.key === 'call_status') {
    return (
      <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${getCallStatusStyle(String(value))}`}>
        {String(value)}
      </span>
    );
  }

  return formatValue(value, field);
}

// Detail row component
function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex py-1.5 border-b last:border-b-0">
      <div className="font-semibold text-muted-foreground w-48 shrink-0">
        {label}:
      </div>
      <div className="flex-1 text-foreground">{value}</div>
    </div>
  );
}

export function LabResultsCallList() {
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('lab_results');
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Sheet states
  const [detailSheetOpen, setDetailSheetOpen] = useState(false);
  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [patientToDelete, setPatientToDelete] = useState<Patient | null>(null);

  const loadPatients = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getPatients('lab_results');
      setPatients(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load calls');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPatients();
  }, [loadPatients]);

  // Poll only active call statuses (not full page reload)
  useEffect(() => {
    const activePatientIds = patients
      .filter(p => p.call_status === 'Dialing' || p.call_status === 'In Progress')
      .map(p => p.patient_id);

    if (activePatientIds.length === 0) return;

    const interval = setInterval(async () => {
      for (const patientId of activePatientIds) {
        try {
          const updatedPatient = await getPatient(patientId);
          setPatients(prev => prev.map(p =>
            p.patient_id === patientId
              ? { ...p, call_status: updatedPatient.call_status }
              : p
          ));
        } catch {
          // Patient fetch failed, skip
        }
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [patients]);

  const handleViewPatient = async (patient: Patient) => {
    try {
      const patientData = await getPatient(patient.patient_id);
      setSelectedPatient(patientData);

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
        } catch {
          setTranscript([]);
        }
      } else {
        setTranscript([]);
      }

      setDetailSheetOpen(true);
    } catch {
      toast.error('Failed to load call details');
    }
  };

  const handleDeletePatientSingle = (patient: Patient) => {
    setPatientToDelete(patient);
    setDeleteDialogOpen(true);
  };

  const confirmDelete = async () => {
    if (!patientToDelete) return;

    try {
      await deletePatient(patientToDelete.patient_id);
      toast.success('Call record deleted');
      setDeleteDialogOpen(false);
      setPatientToDelete(null);
      await loadPatients();
    } catch {
      toast.error('Failed to delete call record');
    }
  };

  const handleDeletePatients = async (selectedPatients: Patient[]) => {
    let successCount = 0;
    let failCount = 0;

    for (const patient of selectedPatients) {
      try {
        await deletePatient(patient.patient_id);
        successCount++;
      } catch {
        failCount++;
      }
    }

    await loadPatients();
    toast.success(`Deleted: ${successCount} success, ${failCount} failed`);
  };

  if (!schema) {
    return (
      <WorkflowLayout workflowName="lab_results" title="Calls">
        <p className="text-muted-foreground">Loading schema...</p>
      </WorkflowLayout>
    );
  }

  if (error) {
    return (
      <WorkflowLayout workflowName="lab_results" title="Calls">
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error}</p>
          <Button onClick={loadPatients} variant="outline">
            Retry
          </Button>
        </div>
      </WorkflowLayout>
    );
  }

  // Get all fields from schema, sorted by display_order
  const allFields = [...schema.patient_schema.fields].sort((a, b) => a.display_order - b.display_order);

  return (
    <WorkflowLayout
      workflowName="lab_results"
      title="Calls"
      actions={
        <Button onClick={loadPatients} variant="outline" size="sm">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      }
    >
      <div className="space-y-4">
        <p className="text-muted-foreground">
          {patients.length} call(s)
        </p>
        <DynamicTable
          schema={schema}
          patients={patients}
          onRowClick={handleViewPatient}
          loading={loading}
          onViewPatient={handleViewPatient}
          onDeletePatient={handleDeletePatientSingle}
          onDeletePatients={handleDeletePatients}
        />
      </div>

      {/* Call Detail Sheet */}
      <Sheet open={detailSheetOpen} onOpenChange={setDetailSheetOpen}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{selectedPatient?.patient_name || 'Call Details'}</SheetTitle>
            <SheetDescription>
              View call information and transcript
            </SheetDescription>
          </SheetHeader>

          {selectedPatient && (
            <div className="mt-6 space-y-4">
              {/* Call Information Card */}
              <div className="bg-card rounded-lg border p-4 space-y-3">
                <h3 className="text-lg font-semibold text-primary mb-3">
                  Call Information
                </h3>
                <div className="space-y-0">
                  {allFields.map(field => (
                    <DetailRow
                      key={field.key}
                      label={field.label}
                      value={renderFieldValue(selectedPatient[field.key], field)}
                    />
                  ))}
                </div>
              </div>

              {/* Call Transcript Card */}
              {transcript.length > 0 ? (
                <div className="bg-card rounded-lg border p-4">
                  <h3 className="text-lg font-semibold text-primary mb-3">
                    Call Transcript
                  </h3>
                  <TranscriptViewer messages={transcript} callerLabel="Patient" />
                </div>
              ) : selectedPatient.call_status === 'Completed' ? (
                <div className="bg-card rounded-lg border p-4 text-center">
                  <h3 className="text-lg font-semibold text-primary mb-3">
                    Call Transcript
                  </h3>
                  <p className="text-muted-foreground">No transcript available for this call.</p>
                </div>
              ) : null}
            </div>
          )}
        </SheetContent>
      </Sheet>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Call Record?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete this call record.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </WorkflowLayout>
  );
}
