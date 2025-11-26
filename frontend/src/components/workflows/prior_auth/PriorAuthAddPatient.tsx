import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DynamicForm } from '../shared/DynamicForm';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { useOrganization } from '@/contexts/OrganizationContext';
import { addPatient, addPatientsBulk } from '@/api';
import { toast } from 'sonner';

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

  const handleBulkSubmit = async (patients: Record<string, unknown>[]) => {
    try {
      // Add workflow to each patient and strip any computed fields that might be in the CSV
      const patientsWithWorkflow = patients.map(p => {
        const { prior_auth_status, reference_number, ...rest } = p as Record<string, unknown>;
        return { ...rest, workflow: 'prior_auth' };
      });

      const response = await addPatientsBulk(patientsWithWorkflow);

      if (response.success_count > 0) {
        toast.success(`Successfully added ${response.success_count} patient(s)`);
      }

      if (response.failed_count > 0) {
        toast.error(`${response.failed_count} patient(s) failed to add`);
        if (response.errors) {
          response.errors.forEach((error) => {
            toast.error(`Row ${error.row} (${error.patient_name || 'Unknown'}): ${error.error}`);
          });
        }
      }

      // Navigate back to list if at least some succeeded
      if (response.success_count > 0) {
        navigate('/workflows/prior_auth/patients');
      }
    } catch (err: any) {
      console.error('Error uploading CSV:', err);

      // Extract validation errors from response
      if (err.response?.data?.detail && Array.isArray(err.response.data.detail)) {
        const errors = err.response.data.detail;
        const errorMessages = errors.map((e: any) => {
          const location = e.loc || [];
          const rowIndex = location.find((loc: any) => typeof loc === 'number');
          const field = location[location.length - 1];
          const msg = e.msg || 'Validation error';

          if (rowIndex !== undefined) {
            return `Row ${rowIndex + 1}, field "${field}": ${msg}`;
          }
          return `Field "${field}": ${msg}`;
        }).join('\n');

        toast.error(`Validation errors:\n${errorMessages}`, { duration: 10000 });
      } else {
        const errorMsg = err.response?.data?.detail || err.message || 'Failed to upload CSV file';
        toast.error(errorMsg);
      }
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
      <div className="flex justify-center">
        <div className="w-full max-w-2xl">
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
            showCsvUpload={true}
            onBulkSubmit={handleBulkSubmit}
          />
        </div>
      </div>
    </WorkflowLayout>
  );
}
