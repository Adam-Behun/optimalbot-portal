import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { DynamicTable } from '../shared/DynamicTable';
import { DynamicForm } from '../shared/DynamicForm';
import { TranscriptViewer } from '../shared/TranscriptViewer';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient, SchemaField, TranscriptMessage } from '@/types';
import { getPatients, getPatient, startCall, deletePatient, updatePatient } from '@/api';
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
import { RefreshCw, Phone, Edit, Loader2 } from 'lucide-react';
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

export function PriorAuthPatientList() {
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('prior_auth');
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Sheet states
  const [detailSheetOpen, setDetailSheetOpen] = useState(false);
  const [editSheetOpen, setEditSheetOpen] = useState(false);
  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [callLoading, setCallLoading] = useState(false);
  const [editLoading, setEditLoading] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [patientToDelete, setPatientToDelete] = useState<Patient | null>(null);
  const [callDialogOpen, setCallDialogOpen] = useState(false);
  const [patientToCall, setPatientToCall] = useState<Patient | null>(null);
  const [patientsToCall, setPatientsToCall] = useState<Patient[]>([]);

  const loadPatients = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getPatients('prior_auth');
      setPatients(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load patients');
      console.error('Error loading patients:', err);
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
      // Fetch only the patients with active calls
      for (const patientId of activePatientIds) {
        try {
          const updatedPatient = await getPatient(patientId);
          setPatients(prev => prev.map(p =>
            p.patient_id === patientId
              ? { ...p, call_status: updatedPatient.call_status }
              : p
          ));
        } catch {
          // Patient fetch failed, skip this one
        }
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [patients]);

  const handleViewPatient = async (patient: Patient) => {
    try {
      const patientData = await getPatient(patient.patient_id);
      setSelectedPatient(patientData);

      // Parse transcript
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
    } catch (err) {
      toast.error('Failed to load patient details');
    }
  };

  const handleEditPatient = async (patient: Patient) => {
    try {
      const patientData = await getPatient(patient.patient_id);
      setSelectedPatient(patientData);
      setEditSheetOpen(true);
    } catch (err) {
      toast.error('Failed to load patient details');
    }
  };

  const handleStartCallSingle = (patient: Patient) => {
    const phoneNumber = patient.insurance_phone || patient.phone;
    if (!phoneNumber) {
      toast.error('No phone number available');
      return;
    }

    setPatientToCall(patient);
    setPatientsToCall([]);
    setCallDialogOpen(true);
  };

  const handleDeletePatientSingle = (patient: Patient) => {
    setPatientToDelete(patient);
    setDeleteDialogOpen(true);
  };

  const confirmDelete = async () => {
    if (!patientToDelete) return;

    try {
      await deletePatient(patientToDelete.patient_id);
      toast.success('Patient deleted');
      setDeleteDialogOpen(false);
      setPatientToDelete(null);
      await loadPatients();
    } catch (err) {
      toast.error('Failed to delete patient');
    }
  };

  const handleStartCalls = (selectedPatients: Patient[]) => {
    const eligiblePatients = selectedPatients.filter(
      p => p.call_status === 'Not Started' && (p.insurance_phone || p.phone)
    );

    if (eligiblePatients.length === 0) {
      toast.warning('No eligible patients selected');
      return;
    }

    setPatientToCall(null);
    setPatientsToCall(eligiblePatients);
    setCallDialogOpen(true);
  };

  const confirmStartCall = async () => {
    if (patientToCall) {
      // Single patient call - optimistic update
      const patientId = patientToCall.patient_id;
      setPatients(prev => prev.map(p =>
        p.patient_id === patientId ? { ...p, call_status: 'Dialing' as const } : p
      ));
      setCallDialogOpen(false);
      setPatientToCall(null);

      try {
        const phoneNumber = patientToCall.insurance_phone || patientToCall.phone;
        await startCall(patientId, phoneNumber, 'prior_auth');
        toast.success('Call started');
      } catch (err) {
        // Revert on failure
        setPatients(prev => prev.map(p =>
          p.patient_id === patientId ? { ...p, call_status: 'Not Started' as const } : p
        ));
        toast.error('Failed to start call');
      }
    } else if (patientsToCall.length > 0) {
      // Bulk calls - optimistic update
      const patientIds = patientsToCall.map(p => p.patient_id);
      setPatients(prev => prev.map(p =>
        patientIds.includes(p.patient_id) ? { ...p, call_status: 'Dialing' as const } : p
      ));
      setCallDialogOpen(false);
      setPatientsToCall([]);

      let successCount = 0;
      let failCount = 0;
      const failedIds: string[] = [];

      for (const patient of patientsToCall) {
        try {
          const phoneNumber = patient.insurance_phone || patient.phone;
          await startCall(patient.patient_id, phoneNumber, 'prior_auth');
          successCount++;
        } catch (err) {
          failCount++;
          failedIds.push(patient.patient_id);
        }
      }

      // Revert failed ones
      if (failedIds.length > 0) {
        setPatients(prev => prev.map(p =>
          failedIds.includes(p.patient_id) ? { ...p, call_status: 'Not Started' as const } : p
        ));
      }

      toast.success(`Calls started: ${successCount} success, ${failCount} failed`);
    } else {
      setCallDialogOpen(false);
      setPatientToCall(null);
      setPatientsToCall([]);
    }
  };

  const handleDeletePatients = async (selectedPatients: Patient[]) => {
    let successCount = 0;
    let failCount = 0;

    for (const patient of selectedPatients) {
      try {
        await deletePatient(patient.patient_id);
        successCount++;
      } catch (err) {
        failCount++;
      }
    }

    await loadPatients();
    toast.success(`Deleted: ${successCount} success, ${failCount} failed`);
  };

  const handleStartCallFromSheet = async () => {
    if (!selectedPatient) return;
    const phoneNumber = selectedPatient.insurance_phone || selectedPatient.phone;
    if (!phoneNumber) {
      toast.error('No phone number available');
      return;
    }

    const patientId = selectedPatient.patient_id;

    // Optimistic update
    setSelectedPatient(prev => prev ? { ...prev, call_status: 'Dialing' as const } : prev);
    setPatients(prev => prev.map(p =>
      p.patient_id === patientId ? { ...p, call_status: 'Dialing' as const } : p
    ));

    try {
      setCallLoading(true);
      await startCall(patientId, phoneNumber, 'prior_auth');
      toast.success('Call started');
    } catch (err) {
      // Revert on failure
      setSelectedPatient(prev => prev ? { ...prev, call_status: 'Not Started' as const } : prev);
      setPatients(prev => prev.map(p =>
        p.patient_id === patientId ? { ...p, call_status: 'Not Started' as const } : p
      ));
      toast.error('Failed to start call');
    } finally {
      setCallLoading(false);
    }
  };

  const handleEditSubmit = async (formData: Record<string, unknown>) => {
    if (!selectedPatient) return;

    try {
      setEditLoading(true);
      await updatePatient(selectedPatient.patient_id, formData);
      toast.success('Patient updated');
      setEditSheetOpen(false);
      await loadPatients();
    } catch (err) {
      toast.error('Failed to update patient');
    } finally {
      setEditLoading(false);
    }
  };

  if (!schema) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Patients">
        <p className="text-muted-foreground">Loading schema...</p>
      </WorkflowLayout>
    );
  }

  if (error) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Patients">
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error}</p>
          <Button onClick={loadPatients} variant="outline">
            Retry
          </Button>
        </div>
      </WorkflowLayout>
    );
  }

  // Separate computed and non-computed fields for detail view
  const allFields = [...schema.patient_schema.fields].sort((a, b) => a.display_order - b.display_order);
  const regularFields = allFields.filter(f => !f.computed);
  const computedFields = allFields.filter(f => f.computed);

  return (
    <WorkflowLayout
      workflowName="prior_auth"
      title="Patients"
      actions={
        <div className="flex gap-2">
          <Button onClick={loadPatients} variant="outline" size="sm">
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
          <Button onClick={() => navigate('/workflows/prior_auth/patients/add')}>
            Add Patient
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <p className="text-muted-foreground">
          {patients.length} patient(s)
        </p>
        <DynamicTable
          schema={schema}
          patients={patients}
          loading={loading}
          onRowClick={handleViewPatient}
          onStartCalls={handleStartCalls}
          onDeletePatients={handleDeletePatients}
          onViewPatient={handleViewPatient}
          onEditPatient={handleEditPatient}
          onStartCall={handleStartCallSingle}
          onDeletePatient={handleDeletePatientSingle}
        />
      </div>

      {/* Patient Detail Sheet */}
      <Sheet open={detailSheetOpen} onOpenChange={setDetailSheetOpen}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{selectedPatient?.patient_name || 'Patient Details'}</SheetTitle>
            <SheetDescription>
              View patient information and call transcript
            </SheetDescription>
          </SheetHeader>

          {selectedPatient && (
            <div className="mt-6 space-y-4">
              {/* Actions */}
              <div className="flex gap-2">
                <Button
                  onClick={handleStartCallFromSheet}
                  disabled={callLoading || selectedPatient.call_status === 'In Progress'}
                  size="sm"
                >
                  {callLoading ? (
                    <><Loader2 className="h-4 w-4 animate-spin mr-2" /> Starting...</>
                  ) : (
                    <><Phone className="h-4 w-4 mr-2" /> Start Call</>
                  )}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setDetailSheetOpen(false);
                    setEditSheetOpen(true);
                  }}
                >
                  <Edit className="h-4 w-4 mr-2" /> Edit
                </Button>
              </div>

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
                      value={formatValue(selectedPatient[field.key], field)}
                    />
                  ))}
                  {/* Authorization Status fields inline */}
                  {computedFields.map(field => (
                    <DetailRow
                      key={field.key}
                      label={field.label}
                      value={formatValue(selectedPatient[field.key], field)}
                    />
                  ))}
                  <DetailRow
                    label="Call Status"
                    value={
                      <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${
                        selectedPatient.call_status === 'Completed' || selectedPatient.call_status === 'Supervisor Dialed'
                          ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
                          : selectedPatient.call_status === 'In Progress' || selectedPatient.call_status === 'Dialing'
                            ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200'
                            : selectedPatient.call_status === 'Failed'
                              ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
                              : 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200'
                      }`}>
                        {selectedPatient.call_status}
                      </span>
                    }
                  />
                </div>
              </div>

              {/* Call Transcript Card */}
              {transcript.length > 0 ? (
                <div className="bg-card rounded-lg border p-4">
                  <h3 className="text-lg font-semibold text-primary mb-3">
                    Call Transcript
                  </h3>
                  <TranscriptViewer messages={transcript} />
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

      {/* Edit Patient Sheet */}
      <Sheet open={editSheetOpen} onOpenChange={setEditSheetOpen}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Edit Patient</SheetTitle>
            <SheetDescription>
              Update patient information
            </SheetDescription>
          </SheetHeader>

          {selectedPatient && (
            <div className="mt-6">
              <DynamicForm
                schema={schema}
                initialData={selectedPatient}
                onSubmit={handleEditSubmit}
                onCancel={() => setEditSheetOpen(false)}
                submitLabel="Save Changes"
                loading={editLoading}
              />
            </div>
          )}
        </SheetContent>
      </Sheet>

      {/* Start Call Confirmation Dialog */}
      <AlertDialog open={callDialogOpen} onOpenChange={setCallDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Start Call?</AlertDialogTitle>
            <AlertDialogDescription>
              {patientToCall
                ? `This will initiate a call for ${patientToCall.patient_name}.`
                : `This will initiate calls for ${patientsToCall.length} patient(s).`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmStartCall}>
              Start Call{patientsToCall.length > 1 ? 's' : ''}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Patient?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete {patientToDelete?.patient_name}'s record.
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
