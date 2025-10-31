import { useNavigate, Link, useLocation } from 'react-router-dom';
import { useForm } from '@tanstack/react-form';
import { addPatient, addPatientsBulk } from '../api';
import { AddPatientFormData } from '../types';
import { Button } from '@/components/ui/button';
import { ButtonGroup } from '@/components/ui/button-group';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SettingsMenu } from "@/components/settings-menu";
import { toast } from "sonner";
import { Download, Upload } from "lucide-react";
import { useRef } from 'react';
import Papa from 'papaparse';

const AddPatientForm = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const isActive = (path: string) => location.pathname === path;
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDownloadSample = () => {
    const exampleCSV = `patient_name,date_of_birth,insurance_member_id,insurance_company_name,insurance_phone,supervisor_phone,facility_name,cpt_code,provider_npi,provider_name,appointment_time
John Doe,1990-05-15,ABC123456789,Blue Cross Blue Shield,+11234567890,+11234567899,City Medical Center,99213,1234567890,Dr. Jane Smith,2025-10-15T10:00
Jane Smith,1985-08-20,XYZ987654321,Aetna,+19876543210,+19876543219,Community Hospital,99214,0987654321,Dr. John Johnson,2025-10-16T14:30`;

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
          const patients = results.data as AddPatientFormData[];

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
    defaultValues: {
      patient_name: '',
      date_of_birth: '',
      insurance_member_id: '',
      insurance_company_name: '',
      insurance_phone: '',
      supervisor_phone: '',
      facility_name: '',
      cpt_code: '',
      provider_npi: '',
      provider_name: '',
      appointment_time: '',
    },
    onSubmit: async ({ value }) => {
      try {
        const response = await addPatient(value);
        toast.success(`Patient ${response.patient_name} added successfully!`);
        navigate('/');
      } catch (err) {
        console.error('Error adding patient:', err);
        toast.error('Failed to add patient. Please try again.');
      }
    },
  });

  return (
    <div className="max-w-5xl mx-auto py-8 px-4 space-y-6">
      {/* Navigation and Controls */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-6">
          <Link
            to="/"
            className={`px-4 py-2 inline-block transition-all border-b-2 ${
              isActive('/')
                ? 'text-primary border-primary font-semibold'
                : 'text-muted-foreground border-transparent hover:text-foreground'
            }`}
          >
            Patients
          </Link>
          <Link
            to="/add-patient"
            className={`px-4 py-2 inline-block transition-all border-b-2 ${
              isActive('/add-patient')
                ? 'text-primary border-primary font-semibold'
                : 'text-muted-foreground border-transparent hover:text-foreground'
            }`}
          >
            Add Patient
          </Link>
        </div>
        <SettingsMenu />
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Patient Name
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Date of Birth
                </Label>
                <div className="flex-1">
                  <Input
                    type="date"
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Facility
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Insurance Company
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Insurance Member ID
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Insurance Phone
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    placeholder="+15551234567"
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Supervisor Phone (Optional)
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    placeholder="+15551234567"
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  CPT Code
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Provider NPI
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
              <div className="flex items-center py-1.5 border-b">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Provider Name
                </Label>
                <div className="flex-1">
                  <Input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
                const minTime = new Date(now.getTime() + 60 * 60 * 1000); // 1 hour from now
                const maxTime = new Date(now.getTime() + 90 * 24 * 60 * 60 * 1000); // 3 months from now

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
              <div className="flex items-center py-1.5 border-b last:border-b-0">
                <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                  Appointment Time
                </Label>
                <div className="flex-1">
                  <Input
                    type="datetime-local"
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onBlur={field.handleBlur}
                    onChange={(e) => field.handleChange(e.target.value)}
                    className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8"
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
  );
};

export default AddPatientForm;