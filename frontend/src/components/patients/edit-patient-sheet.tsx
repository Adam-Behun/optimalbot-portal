import { useForm } from '@tanstack/react-form';
import { updatePatient } from '@/api';
import { Patient, AddPatientFormData } from '@/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';

interface EditPatientSheetProps {
  patient: Patient | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave?: () => void;
}

export function EditPatientSheet({
  patient,
  open,
  onOpenChange,
  onSave,
}: EditPatientSheetProps) {
  if (!patient) return null;

  // Convert MM/DD/YYYY to YYYY-MM-DD for date input
  const formatDateForInput = (dateStr: string): string => {
    if (!dateStr) return '';
    const parts = dateStr.split('/');
    if (parts.length === 3) {
      return `${parts[2]}-${parts[0].padStart(2, '0')}-${parts[1].padStart(2, '0')}`;
    }
    return dateStr;
  };

  // Convert MM/DD/YYYY HH:MM AM/PM to YYYY-MM-DDTHH:mm for datetime-local input
  const formatDateTimeForInput = (dateTimeStr: string): string => {
    if (!dateTimeStr) return '';
    const match = dateTimeStr.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
    if (match) {
      const [, month, day, year, hours, minutes, ampm] = match;
      let hour = parseInt(hours, 10);
      if (ampm.toUpperCase() === 'PM' && hour !== 12) hour += 12;
      if (ampm.toUpperCase() === 'AM' && hour === 12) hour = 0;
      return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}T${hour.toString().padStart(2, '0')}:${minutes}`;
    }
    return dateTimeStr.slice(0, 16);
  };

  const form = useForm({
    defaultValues: {
      patient_name: patient.patient_name || '',
      date_of_birth: formatDateForInput(patient.date_of_birth || ''),
      insurance_member_id: patient.insurance_member_id || '',
      insurance_company_name: patient.insurance_company_name || '',
      insurance_phone: patient.insurance_phone || '',
      supervisor_phone: patient.supervisor_phone || '',
      facility_name: patient.facility_name || '',
      cpt_code: patient.cpt_code || '',
      provider_npi: patient.provider_npi || '',
      provider_name: patient.provider_name || '',
      appointment_time: formatDateTimeForInput(patient.appointment_time || ''),
    } as AddPatientFormData,
    onSubmit: async ({ value }) => {
      try {
        await updatePatient(patient.patient_id, value);
        toast.success('Patient updated successfully');
        onOpenChange(false);
        onSave?.();
      } catch (err) {
        console.error('Error updating patient:', err);
        toast.error('Failed to update patient');
      }
    },
  });

  return (
    <Sheet key={patient.patient_id} open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <SheetHeader>
          <SheetTitle>{patient.patient_name}</SheetTitle>
          <SheetDescription>
            Patient ID: {patient.patient_id}
          </SheetDescription>
        </SheetHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            form.handleSubmit();
          }}
          className="mt-4"
        >
          <div className="bg-card rounded-lg border p-4 space-y-3">
            <h3 className="text-lg font-semibold text-primary mb-3">
              Edit Patient Information
            </h3>
            <div className="space-y-0">
          <form.Field
            name="patient_name"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'Patient name is required';
                if (!/^[a-zA-Z\s\-'.,]+$/.test(value.trim())) {
                  return 'Patient name must contain only English alphabet characters';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Patient Name:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="date_of_birth"
            validators={{
              onChange: ({ value }) => {
                if (!value) return 'Date of birth is required';
                const dob = new Date(value);
                const yesterday = new Date();
                yesterday.setDate(yesterday.getDate() - 1);
                yesterday.setHours(23, 59, 59, 999);
                if (dob > yesterday) {
                  return 'Date of birth must be at least yesterday or earlier';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Date of Birth:
                </div>
                <div className="flex-1">
                  <Input
                    type="date"
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="facility_name"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'Facility name is required';
                if (!/^[a-zA-Z\s\-'.,]+$/.test(value.trim())) {
                  return 'Facility name must contain only English alphabet characters';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Facility:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="insurance_company_name"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'Insurance company is required';
                if (!/^[a-zA-Z\s\-'.,]+$/.test(value.trim())) {
                  return 'Insurance company must contain only English alphabet characters';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Insurance Company:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="insurance_member_id"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'Member ID is required';
                if (!/^[a-zA-Z0-9]+$/.test(value.trim())) {
                  return 'Member ID must contain only letters and numbers';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Insurance Member ID:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="insurance_phone"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'Insurance phone is required';
                const cleaned = value.trim().replace(/[\s\-]/g, '');
                if (cleaned.startsWith('+1')) {
                  const digits = cleaned.substring(2);
                  if (!/^\d{10}$/.test(digits)) {
                    return 'Phone must have exactly 10 digits after +1 (e.g., +15551234567)';
                  }
                } else if (cleaned.startsWith('1')) {
                  const digits = cleaned.substring(1);
                  if (!/^\d{10}$/.test(digits)) {
                    return 'Phone must have exactly 10 digits after 1 (e.g., 15551234567)';
                  }
                } else {
                  return 'Phone must start with +1 or 1 (e.g., +15551234567 or 15551234567)';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Insurance Phone:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    placeholder="+15551234567"
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="supervisor_phone"
            validators={{
              onChange: ({ value }) => {
                if (!value || value.trim() === '') return undefined;
                const cleaned = value.trim().replace(/[\s\-]/g, '');
                if (cleaned.startsWith('+1')) {
                  const digits = cleaned.substring(2);
                  if (!/^\d{10}$/.test(digits)) {
                    return 'Phone must have exactly 10 digits after +1 (e.g., +15551234567)';
                  }
                } else if (cleaned.startsWith('1')) {
                  const digits = cleaned.substring(1);
                  if (!/^\d{10}$/.test(digits)) {
                    return 'Phone must have exactly 10 digits after 1 (e.g., 15551234567)';
                  }
                } else {
                  return 'Phone must start with +1 or 1 (e.g., +15551234567 or 15551234567)';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Supervisor Phone:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    placeholder="+15551234567"
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="cpt_code"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'CPT code is required';
                if (!/^\d+$/.test(value.trim())) {
                  return 'CPT code must contain only integers';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  CPT Code:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="provider_npi"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'Provider NPI is required';
                if (!/^\d+$/.test(value.trim())) {
                  return 'Provider NPI must contain only integers';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Provider NPI:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="provider_name"
            validators={{
              onChange: ({ value }) => {
                if (!value || !value.trim()) return 'Provider name is required';
                if (!/^[a-zA-Z\s\-'.,]+$/.test(value.trim())) {
                  return 'Provider name must contain only English alphabet characters';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b">
                <div className="font-semibold text-muted-foreground w-48">
                  Provider Name:
                </div>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />

          <form.Field
            name="appointment_time"
            validators={{
              onChange: ({ value }) => {
                if (!value) return 'Appointment time is required';
                const appt = new Date(value);
                const now = new Date();
                const minTime = new Date(now.getTime() + 60 * 60 * 1000);
                const maxTime = new Date(now.getTime() + 90 * 24 * 60 * 60 * 1000);
                if (appt < minTime) {
                  return 'Appointment must be at least 1 hour from now';
                }
                if (appt > maxTime) {
                  return 'Appointment must be within 3 months from now';
                }
                return undefined;
              },
            }}
            children={(field) => (
              <div className="flex py-1.5 border-b last:border-b-0">
                <div className="font-semibold text-muted-foreground w-48">
                  Appointment Time:
                </div>
                <div className="flex-1">
                  <Input
                    type="datetime-local"
                    id={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                  />
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            )}
          />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit">
              Save Changes
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
