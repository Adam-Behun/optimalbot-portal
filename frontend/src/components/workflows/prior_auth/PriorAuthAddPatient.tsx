import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DynamicForm } from '../shared/DynamicForm';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { addPatient } from '@/api';

export function PriorAuthAddPatient() {
  const navigate = useNavigate();
  const { getWorkflowSchema } = useOrganization();
  const schema = getWorkflowSchema('prior_auth');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (formData: Record<string, unknown>) => {
    try {
      setLoading(true);
      setError(null);
      await addPatient({ ...formData, workflow: 'prior_auth' });
      navigate('/workflows/prior_auth/patients');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  if (!schema) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Add Patient">
        <p className="text-muted-foreground">Loading schema...</p>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout workflowName="prior_auth" title="Add Patient">
      <div className="max-w-2xl">
        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-md text-red-700">
            {error}
          </div>
        )}
        <DynamicForm
          schema={schema}
          onSubmit={handleSubmit}
          onCancel={() => navigate('/workflows/prior_auth/patients')}
          submitLabel="Add Patient"
          loading={loading}
        />
      </div>
    </WorkflowLayout>
  );
}
