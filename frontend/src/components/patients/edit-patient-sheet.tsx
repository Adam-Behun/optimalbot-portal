import { useForm } from '@tanstack/react-form';
import { useMemo } from 'react';
import { updatePatient } from '@/api';
import { Patient, SchemaField } from '@/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from 'sonner';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { getCurrentWorkflowSchema } from '@/lib/auth';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
  // Get workflow schema
  const schemaFields = useMemo(() => {
    const schema = getCurrentWorkflowSchema();
    const fields = schema?.fields || [];
    return [...fields].sort((a, b) => a.display_order - b.display_order);
  }, []);

  // Build default values from patient data - flat fields
  const defaultValues = useMemo(() => {
    if (!patient) return {};

    // Convert MM/DD/YYYY to YYYY-MM-DD for date input
    const formatDateForInput = (dateStr: string): string => {
      if (!dateStr) return '';
      const parts = dateStr.split('/');
      if (parts.length === 3) {
        return `${parts[2]}-${parts[0].padStart(2, '0')}-${parts[1].padStart(2, '0')}`;
      }
      return dateStr;
    };

    const defaults: Record<string, string> = {};

    // Add all fields from schema - read flat from patient
    schemaFields.forEach((field: SchemaField) => {
      const value = patient[field.key];
      // Format dates for input
      if (field.type === 'date' && value) {
        defaults[field.key] = formatDateForInput(value);
      } else {
        defaults[field.key] = value || field.default || '';
      }
    });

    return defaults;
  }, [patient, schemaFields]);

  const form = useForm({
    defaultValues,
    onSubmit: async ({ value }) => {
      if (!patient) return;
      try {
        // Send all fields flat
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

  if (!patient) return null;

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
              {/* All fields from schema */}
              {schemaFields.map((schemaField: SchemaField) => (
                <form.Field
                  key={schemaField.key}
                  name={schemaField.key}
                  validators={{
                    onChange: ({ value }) => {
                      if (schemaField.required && (!value || !value.trim())) {
                        return `${schemaField.label} is required`;
                      }
                      return undefined;
                    },
                  }}
                  children={(field) => (
                    <div className="flex py-1.5 border-b">
                      <Label className="font-semibold text-muted-foreground w-48">
                        {schemaField.label}:
                      </Label>
                      <div className="flex-1">
                        {schemaField.type === 'select' && schemaField.options ? (
                          <Select
                            value={field.state.value}
                            onValueChange={(value) => field.handleChange(value)}
                          >
                            <SelectTrigger className="border-0 focus:ring-0 focus:ring-offset-0 h-8">
                              <SelectValue placeholder={`Select ${schemaField.label}`} />
                            </SelectTrigger>
                            <SelectContent>
                              {schemaField.options.map((option) => (
                                <SelectItem key={option} value={option}>
                                  {option}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        ) : (
                          <Input
                            type={schemaField.type === 'date' ? 'date' : 'text'}
                            id={field.name}
                            value={field.state.value}
                            onBlur={field.handleBlur}
                            onChange={(e) => field.handleChange(e.target.value)}
                            className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 py-0"
                          />
                        )}
                        {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                          <p className="text-sm text-destructive mt-1">
                            {field.state.meta.errors.join(', ')}
                          </p>
                        )}
                      </div>
                    </div>
                  )}
                />
              ))}
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
