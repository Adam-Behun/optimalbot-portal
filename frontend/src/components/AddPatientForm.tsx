import { useNavigate, Link, useLocation } from 'react-router-dom';
import { useForm } from '@tanstack/react-form';
import { addPatient } from '../api';
import { Button } from '@/components/ui/button';
import { ButtonGroup } from '@/components/ui/button-group';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { ModeToggle } from "@/components/mode-toggle";
import { toast } from "sonner";
import { Download, Upload, ChevronDownIcon } from "lucide-react";
import { useRef, useState } from 'react';

const AddPatientForm = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const isActive = (path: string) => location.pathname === path;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dobOpen, setDobOpen] = useState(false);
  const [appointmentDateOpen, setAppointmentDateOpen] = useState(false);

  const handleDownloadSample = () => {
    const exampleCSV = `patient_name,date_of_birth,insurance_member_id,insurance_company_name,insurance_phone,facility_name,cpt_code,provider_npi,provider_name,appointment_time
John Doe,1990-05-15,ABC123456789,Blue Cross Blue Shield,+11234567890,City Medical Center,99213,1234567890,Dr. Jane Smith,2025-10-15T10:00
Jane Smith,1985-08-20,XYZ987654321,Aetna,+19876543210,Community Hospital,99214,0987654321,Dr. John Johnson,2025-10-16T14:30`;

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

  const form = useForm({
    defaultValues: {
      patient_name: '',
      date_of_birth: '',
      insurance_member_id: '',
      insurance_company_name: '',
      insurance_phone: '',
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
        <ModeToggle />
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
            children={(field) => {
              const dateValue = field.state.value ? new Date(field.state.value) : undefined;
              return (
                <div className="flex items-center py-1.5 border-b">
                  <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                    Date of Birth
                  </Label>
                  <div className="flex-1 flex gap-2">
                    <Popover open={dobOpen} onOpenChange={setDobOpen}>
                      <PopoverTrigger asChild>
                        <Button
                          variant="outline"
                          className="justify-between font-normal border-0 h-8 flex-1"
                        >
                          {dateValue ? dateValue.toLocaleDateString() : "Select date"}
                          <ChevronDownIcon className="h-4 w-4" />
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent className="w-auto overflow-hidden p-0" align="start">
                        <Calendar
                          mode="single"
                          selected={dateValue}
                          captionLayout="dropdown"
                          onSelect={(date) => {
                            if (date) {
                              field.handleChange(date.toISOString().split('T')[0]);
                            }
                            setDobOpen(false);
                          }}
                          fromYear={1900}
                          toYear={new Date().getFullYear()}
                        />
                      </PopoverContent>
                    </Popover>
                  </div>
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1 ml-48">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              );
            }}
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
            children={(field) => {
              const dateTimeValue = field.state.value ? new Date(field.state.value) : undefined;
              const dateOnly = dateTimeValue ? dateTimeValue.toISOString().split('T')[0] : '';
              const timeOnly = dateTimeValue ? dateTimeValue.toTimeString().split(' ')[0].substring(0, 5) : '';

              return (
                <div className="flex items-center py-1.5 border-b last:border-b-0">
                  <Label htmlFor={field.name} className="w-48 text-muted-foreground">
                    Appointment Time
                  </Label>
                  <div className="flex-1 flex gap-2">
                    <Popover open={appointmentDateOpen} onOpenChange={setAppointmentDateOpen}>
                      <PopoverTrigger asChild>
                        <Button
                          variant="outline"
                          className="justify-between font-normal border-0 h-8 w-40"
                        >
                          {dateTimeValue ? dateTimeValue.toLocaleDateString() : "Select date"}
                          <ChevronDownIcon className="h-4 w-4" />
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent className="w-auto overflow-hidden p-0" align="start">
                        <Calendar
                          mode="single"
                          selected={dateTimeValue}
                          captionLayout="dropdown"
                          onSelect={(date) => {
                            if (date) {
                              const newDateTime = new Date(date);
                              if (timeOnly) {
                                const [hours, minutes] = timeOnly.split(':');
                                newDateTime.setHours(parseInt(hours), parseInt(minutes));
                              }
                              field.handleChange(newDateTime.toISOString().slice(0, 16));
                            }
                            setAppointmentDateOpen(false);
                          }}
                          fromYear={new Date().getFullYear()}
                          toYear={new Date().getFullYear() + 5}
                        />
                      </PopoverContent>
                    </Popover>
                    <Input
                      type="time"
                      value={timeOnly}
                      onChange={(e) => {
                        const time = e.target.value;
                        if (dateOnly) {
                          const newDateTime = new Date(dateOnly);
                          const [hours, minutes] = time.split(':');
                          newDateTime.setHours(parseInt(hours), parseInt(minutes));
                          field.handleChange(newDateTime.toISOString().slice(0, 16));
                        }
                      }}
                      className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 w-32 bg-background appearance-none [&::-webkit-calendar-picker-indicator]:hidden"
                    />
                  </div>
                  {field.state.meta.isTouched && field.state.meta.errors.length > 0 && (
                    <p className="text-sm text-destructive mt-1 ml-48">
                      {field.state.meta.errors.join(', ')}
                    </p>
                  )}
                </div>
              );
            }}
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
