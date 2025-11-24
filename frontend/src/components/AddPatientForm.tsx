import { useNavigate } from 'react-router-dom';
import { useForm } from '@tanstack/react-form';
import { addPatient, addPatientsBulk } from '../api';
import { SchemaField } from '../types';
import { Button } from '@/components/ui/button';
import { ButtonGroup } from '@/components/ui/button-group';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Navigation, WorkflowNavigation } from "@/components/Navigation";
import { toast } from "sonner";
import { Download, Upload } from "lucide-react";
import { useRef, useMemo } from 'react';
import Papa from 'papaparse';
import { getOrganization, getSelectedWorkflow, getCurrentWorkflowSchema } from '../lib/auth';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const AddPatientForm = () => {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Get organization, workflow and schema
  const org = getOrganization();
  const selectedWorkflow = getSelectedWorkflow();
  const schemaFields = useMemo(() => {
    const schema = getCurrentWorkflowSchema();
    const fields = schema?.fields || [];
    return [...fields].sort((a, b) => a.display_order - b.display_order);
  }, []);

  // Build default values from schema
  const defaultValues = useMemo(() => {
    const defaults: Record<string, string> = {};
    schemaFields.forEach((field: SchemaField) => {
      defaults[field.key] = field.default || '';
    });
    return defaults;
  }, [schemaFields]);

  const handleDownloadSample = () => {
    // Generate CSV headers from schema
    const baseHeaders = ['patient_name', 'date_of_birth'];
    const customHeaders = schemaFields.map((f: SchemaField) => f.key);
    const allHeaders = [...baseHeaders, ...customHeaders];

    const exampleCSV = `${allHeaders.join(',')}
John Doe,1990-05-15,${customHeaders.map(() => 'example').join(',')}`;

    const blob = new Blob([exampleCSV], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'patient_upload_example.csv';
    a.click();
    window.URL.revokeObjectURL(url);
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      complete: async (results) => {
        try {
          const patients = results.data as Record<string, any>[];

          if (patients.length === 0) {
            toast.error('CSV file is empty');
            return;
          }

          toast.info(`Processing ${patients.length} patients...`);

          const response = await addPatientsBulk(patients);

          if (response.success_count > 0) {
            toast.success(`Successfully added ${response.success_count} patient(s)`);
          }

          if (response.failed_count > 0) {
            toast.error(`${response.failed_count} patient(s) failed to add`);
            if (response.errors) {
              response.errors.forEach((error: any) => {
                toast.error(`Row ${error.row} (${error.patient_name}): ${error.error}`);
              });
            }
          }

          // Reset file input
          if (fileInputRef.current) {
            fileInputRef.current.value = '';
          }
        } catch (error: any) {
          console.error('Error uploading CSV:', error);

          // Extract validation errors from response
          if (error.response?.data?.detail && Array.isArray(error.response.data.detail)) {
            const errors = error.response.data.detail;
            const errorMessages = errors.map((err: any) => {
              // Format: "Row 6 (Quvenzhané Poughkeepsie): insurance_phone - Only US/Canadian numbers allowed"
              const location = err.loc || [];
              const rowIndex = location.find((loc: any) => typeof loc === 'number');
              const field = location[location.length - 1];
              const msg = err.msg || 'Validation error';

              if (rowIndex !== undefined) {
                return `Row ${rowIndex + 1}, field "${field}": ${msg}`;
              }
              return `Field "${field}": ${msg}`;
            }).join('\n');

            toast.error(`Validation errors:\n${errorMessages}`, { duration: 10000 });
          } else {
            const errorMsg = error.response?.data?.detail || error.message || 'Failed to upload CSV file';
            toast.error(errorMsg);
          }
        }
      },
      error: (error) => {
        console.error('Error parsing CSV:', error);
        toast.error('Failed to parse CSV file');
      }
    });
  };

  const form = useForm({
    defaultValues,
    onSubmit: async ({ value }) => {
      try {
        if (!selectedWorkflow) {
          toast.error('No workflow selected. Please select a workflow first.');
          return;
        }
        // Send all fields flat with workflow
        const patientData = {
          workflow: selectedWorkflow,
          ...value,
        };
        const response = await addPatient(patientData);
        toast.success(`Patient added successfully!`);
        navigate('/');
      } catch (err) {
        console.error('Error adding patient:', err);
        toast.error('Failed to add patient. Please try again.');
      }
    },
  });

  return (
    <>
      <Navigation />
      <WorkflowNavigation />
      <div className="max-w-4xl mx-auto py-8 px-4 space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Add Patient</h1>
          <p className="text-muted-foreground mt-1">Add a new patient or upload multiple patients via CSV</p>
        </div>

        {/* CSV Upload Buttons */}
      <div className="flex justify-center">
        <ButtonGroup>
          <Button
            variant="outline"
            size="lg"
            onClick={handleDownloadSample}
            className="w-60"
          >
            <Download className="mr-2 h-5 w-5" />
            Download Sample .csv
          </Button>
          <Button
            variant="outline"
            size="lg"
            onClick={handleUploadClick}
            className="w-60"
          >
            <Upload className="mr-2 h-5 w-5" />
            Upload .csv File
          </Button>
        </ButtonGroup>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={handleFileChange}
        />
      </div>

      {/* Form */}
      <div className="bg-card rounded-lg border">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            form.handleSubmit();
          }}
          className="p-4 space-y-0"
        >
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
                <div className="flex items-center py-1.5 border-b">
                  <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                    {schemaField.label}{!schemaField.required && ' (Optional)'}
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
                        name={field.name}
                        value={field.state.value}
                        onBlur={field.handleBlur}
                        onChange={(e) => field.handleChange(e.target.value)}
                        className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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

          <div className="flex justify-end gap-2 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => form.reset()}
            >
              Reset
            </Button>
            <Button type="submit">
              Add Patient
            </Button>
          </div>
        </form>
      </div>
          </div>
    </>
  );
};

export default AddPatientForm;