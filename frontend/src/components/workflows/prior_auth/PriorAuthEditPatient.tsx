import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { DynamicForm } from '../shared/DynamicForm';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { getPatient, updatePatient } from '@/api';
import { Patient } from '@/types';
import { toast } from 'sonner';

export function PriorAuthEditPatient() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('prior_auth');
  const [patient, setPatient] = useState<Patient | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getPatient(id)
      .then(setPatient)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load patient'))
      .finally(() => setLoading(false));
  }, [id]);

  const handleSubmit = async (formData: Record<string, unknown>) => {
    if (!id) return;
    try {
      setSaving(true);
      setError(null);
      await updatePatient(id, formData);
      toast.success('Patient updated successfully');
      navigate(`/workflows/prior_auth/patients/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      toast.error('Failed to update patient');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Edit Patient">
        <div className="text-center py-10">
          <p className="text-muted-foreground">Loading patient details...</p>
        </div>
      </WorkflowLayout>
    );
  }

  if (!schema || !patient) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Edit Patient">
        <div className="text-center py-10">
          <p className="text-destructive mb-4">{error || 'Patient not found'}</p>
        </div>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout workflowName="prior_auth" title="Edit Patient">
      <div className="space-y-4">
        {/* Header with patient info */}
        <div className="mb-2">
          <h2 className="text-xl font-semibold">{patient.patient_name}</h2>
          <p className="text-sm text-muted-foreground">Patient ID: {patient.patient_id}</p>
        </div>

        {error && (
          <div className="p-4 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        <DynamicForm
          schema={schema}
          initialData={patient}
          onSubmit={handleSubmit}
          onCancel={() => navigate(`/workflows/prior_auth/patients/${id}`)}
          submitLabel="Save Changes"
          loading={saving}
        />
      </div>
    </WorkflowLayout>
  );
}
