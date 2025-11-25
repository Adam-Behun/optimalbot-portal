import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { DynamicTable } from '../shared/DynamicTable';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Patient } from '@/types';
import { getPatients } from '@/api';

export function PatientQuestionsCallList() {
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('patient_questions');
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPatients('patient_questions')
      .then(setPatients)
      .catch(err => setError(err instanceof Error ? err.message : 'Unknown error'))
      .finally(() => setLoading(false));
  }, []);

  if (!schema) {
    return (
      <WorkflowLayout workflowName="patient_questions" title="Inbound Calls">
        <p className="text-muted-foreground">Loading schema...</p>
      </WorkflowLayout>
    );
  }

  if (error) {
    return (
      <WorkflowLayout workflowName="patient_questions" title="Inbound Calls">
        <p className="text-red-500">Error: {error}</p>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout
      workflowName="patient_questions"
      title="Inbound Calls"
    >
      <p className="text-muted-foreground mb-4">
        Records are created automatically when patients call in.
      </p>
      <DynamicTable
        schema={schema}
        patients={patients}
        onRowClick={(patient) => navigate(`/workflows/patient_questions/calls/${patient.patient_id}`)}
        loading={loading}
      />
    </WorkflowLayout>
  );
}
