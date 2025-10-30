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
              onChange: ({ value }) =>
                !value ? 'Patient name is required' : undefined,
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
              onChange: ({ value }) =>
                !value ? 'Date of birth is required' : undefined,
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
              onChange: ({ value }) =>
                !value ? 'Facility name is required' : undefined,
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
              onChange: ({ value }) =>
                !value ? 'Insurance company is required' : undefined,
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
              onChange: ({ value }) =>
                !value ? 'Member ID is required' : undefined,
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
                if (!value) return 'Insurance phone is required';
                if (!/^\+\d{10,15}$/.test(value)) return 'Phone must be +1234567890 format';
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
                    placeholder="+1234567890"
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
                if (value && !/^\+\d{10,15}$/.test(value)) {
                  return 'Phone must be +1234567890 format';
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
                    placeholder="+1234567890"
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
              onChange: ({ value }) =>
                !value ? 'CPT code is required' : undefined,
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
              onChange: ({ value }) =>
                !value ? 'Provider NPI is required' : undefined,
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
              onChange: ({ value }) =>
                !value ? 'Provider name is required' : undefined,
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
              onChange: ({ value }) =>
                !value ? 'Appointment time is required' : undefined,
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