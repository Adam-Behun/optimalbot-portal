import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { DynamicTable } from '../shared/DynamicTable';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient } from '@/types';
import { getPatients, startCall, deletePatient } from '@/api';
import { RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

export function PriorAuthPatientList() {
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('prior_auth');
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  // Auto-refresh when calls are active
  useEffect(() => {
    const hasActiveCalls = patients.some(p => p.call_status === 'In Progress');

    if (hasActiveCalls) {
      const interval = setInterval(() => {
        loadPatients();
      }, 3000);

      return () => clearInterval(interval);
    }
  }, [patients, loadPatients]);

  const handleStartCalls = async (selectedPatients: Patient[]) => {
    const eligiblePatients = selectedPatients.filter(
      p => p.call_status === 'Not Started' && (p.insurance_phone || p.phone)
    );

    if (eligiblePatients.length === 0) {
      toast.warning('No eligible patients selected');
      return;
    }

    const missingPhone = selectedPatients.filter(
      p => p.call_status === 'Not Started' && !p.insurance_phone && !p.phone
    );

    if (missingPhone.length > 0) {
      const proceed = window.confirm(
        `${missingPhone.length} patient(s) missing phone numbers. Continue with ${eligiblePatients.length}?`
      );
      if (!proceed) return;
    }

    const confirmed = window.confirm(
      `Start calls for ${eligiblePatients.length} patient(s)?`
    );

    if (!confirmed) return;

    let successCount = 0;
    let failCount = 0;

    for (const patient of eligiblePatients) {
      try {
        const phoneNumber = patient.insurance_phone || patient.phone;
        await startCall(patient.patient_id, phoneNumber, 'prior_auth');
        successCount++;
      } catch (err) {
        console.error(`Error starting call for ${patient.patient_name}:`, err);
        failCount++;
      }
    }

    await loadPatients();
    toast.success(`Calls started: ${successCount} success, ${failCount} failed`);
  };

  const handleDeletePatients = async (selectedPatients: Patient[]) => {
    let successCount = 0;
    let failCount = 0;

    for (const patient of selectedPatients) {
      try {
        await deletePatient(patient.patient_id);
        successCount++;
      } catch (err) {
        console.error(`Error deleting ${patient.patient_name}:`, err);
        failCount++;
      }
    }

    await loadPatients();
    toast.success(`Deleted: ${successCount} success, ${failCount} failed`);
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
          onRowClick={(patient) => navigate(`/workflows/prior_auth/patients/${patient.patient_id}`)}
          loading={loading}
          onStartCalls={handleStartCalls}
          onDeletePatients={handleDeletePatients}
        />
      </div>
    </WorkflowLayout>
  );
}
