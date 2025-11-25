import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { DynamicTable } from '../shared/DynamicTable';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient } from '@/types';
import { getPatients } from '@/api';

export function PriorAuthPatientList() {
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('prior_auth');
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPatients('prior_auth')
      .then(setPatients)
      .catch(err => setError(err instanceof Error ? err.message : 'Unknown error'))
      .finally(() => setLoading(false));
  }, []);

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
        <p className="text-red-500">Error: {error}</p>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout
      workflowName="prior_auth"
      title="Patients"
      actions={
        <Button onClick={() => navigate('/workflows/prior_auth/patients/add')}>
          Add Patient
        </Button>
      }
    >
      <DynamicTable
        schema={schema}
        patients={patients}
        onRowClick={(patient) => navigate(`/workflows/prior_auth/patients/${patient.patient_id}`)}
        loading={loading}
      />
    </WorkflowLayout>
  );
}
