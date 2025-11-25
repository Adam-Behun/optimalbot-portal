import { useState, FormEvent, useRef } from 'react';
import { WorkflowConfig, SchemaField } from '@/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Download, Upload } from 'lucide-react';
import { toast } from 'sonner';
import Papa from 'papaparse';

interface DynamicFormProps {
  schema: WorkflowConfig;
  initialData?: Record<string, unknown>;
  onSubmit: (data: Record<string, unknown>) => void;
  onCancel?: () => void;
  submitLabel?: string;
  loading?: boolean;
  onBulkSubmit?: (data: Record<string, unknown>[]) => Promise<void>;
  showCsvUpload?: boolean;
}

// Render input based on field type - inline style with borderless inputs
function renderField(
  field: SchemaField,
  value: unknown,
  onChange: (key: string, value: string) => void
) {
  const stringValue = value !== null && value !== undefined ? String(value) : '';

  const inputClassName = "border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-8 bg-transparent";

  switch (field.type) {
    case 'select':
      return (
        <Select
          value={stringValue}
          onValueChange={(val) => onChange(field.key, val)}
        >
          <SelectTrigger className={inputClassName}>
            <SelectValue placeholder={`Select ${field.label.toLowerCase()}`} />
          </SelectTrigger>
          <SelectContent>
            {field.options?.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );

    case 'text':
      return (
        <textarea
          id={field.key}
          value={stringValue}
          onChange={(e) => onChange(field.key, e.target.value)}
          className="flex min-h-[60px] w-full bg-transparent px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          placeholder={field.label}
        />
      );

    case 'date':
      return (
        <Input
          id={field.key}
          type="date"
          value={stringValue}
          onChange={(e) => onChange(field.key, e.target.value)}
          className={inputClassName}
        />
      );

    case 'datetime':
      return (
        <Input
          id={field.key}
          type="datetime-local"
          value={stringValue}
          onChange={(e) => onChange(field.key, e.target.value)}
          className={inputClassName}
        />
      );

    case 'phone':
      return (
        <Input
          id={field.key}
          type="tel"
          value={stringValue}
          onChange={(e) => onChange(field.key, e.target.value)}
          placeholder="+15551234567"
          className={inputClassName}
        />
      );

    default:
      return (
        <Input
          id={field.key}
          type="text"
          value={stringValue}
          onChange={(e) => onChange(field.key, e.target.value)}
          placeholder={field.label}
          className={inputClassName}
        />
      );
  }
}

export function DynamicForm({
  schema,
  initialData = {},
  onSubmit,
  onCancel,
  submitLabel = 'Save',
  loading = false,
  onBulkSubmit,
  showCsvUpload = false,
}: DynamicFormProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Get editable fields (exclude computed), sorted by display_order
  const fields = schema.patient_schema.fields
    .filter((f) => !f.computed)
    .sort((a, b) => a.display_order - b.display_order);

  // Initialize form data with initial values or defaults
  const [formData, setFormData] = useState<Record<string, string>>(() => {
    const data: Record<string, string> = {};
    fields.forEach((field) => {
      const initial = initialData[field.key];
      if (initial !== null && initial !== undefined) {
        data[field.key] = String(initial);
      } else if (field.default) {
        data[field.key] = field.default;
      } else {
        data[field.key] = '';
      }
    });
    return data;
  });

  const [errors, setErrors] = useState<Record<string, string>>({});

  const handleChange = (key: string, value: string) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
    // Clear error when user types
    if (errors[key]) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  };

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    fields.forEach((field) => {
      if (field.required && !formData[field.key]?.trim()) {
        newErrors[field.key] = `${field.label} is required`;
      }
    });
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (validate()) {
      onSubmit(formData);
    }
  };

  const handleReset = () => {
    const data: Record<string, string> = {};
    fields.forEach((field) => {
      if (field.default) {
        data[field.key] = field.default;
      } else {
        data[field.key] = '';
      }
    });
    setFormData(data);
    setErrors({});
  };

  // CSV Upload handlers
  const handleDownloadSample = () => {
    const headers = fields.map(f => f.key).join(',');
    const exampleRow = fields.map(f => {
      switch (f.type) {
        case 'date': return '1990-01-15';
        case 'datetime': return '2025-01-15T10:00';
        case 'phone': return '+11234567890';
        default: return `Example ${f.label}`;
      }
    }).join(',');

    const csv = `${headers}\n${exampleRow}`;
    const blob = new Blob([csv], { type: 'text/csv' });
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

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !onBulkSubmit) return;

    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      complete: async (results) => {
        try {
          const patients = results.data as Record<string, unknown>[];

          if (patients.length === 0) {
            toast.error('CSV file is empty');
            return;
          }

          toast.info(`Processing ${patients.length} patients...`);
          await onBulkSubmit(patients);

          // Reset file input
          if (fileInputRef.current) {
            fileInputRef.current.value = '';
          }
        } catch (error: any) {
          console.error('Error uploading CSV:', error);
          const errorMsg = error.response?.data?.detail || error.message || 'Failed to upload CSV file';
          toast.error(errorMsg);
        }
      },
      error: (error) => {
        console.error('Error parsing CSV:', error);
        toast.error('Failed to parse CSV file');
      }
    });
  };

  return (
    <div className="space-y-6">
      {/* CSV Upload Buttons */}
      {showCsvUpload && onBulkSubmit && (
        <div className="flex justify-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={handleDownloadSample}
            className="w-60"
          >
            <Download className="mr-2 h-5 w-5" />
            Download Sample .csv
          </Button>
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={handleUploadClick}
            className="w-60"
          >
            <Upload className="mr-2 h-5 w-5" />
            Upload .csv File
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={handleFileChange}
          />
        </div>
      )}

      {/* Form */}
      <div className="bg-card rounded-lg border">
        <form onSubmit={handleSubmit} className="p-4 space-y-0">
          {fields.map((field) => (
            <div key={field.key} className="flex items-center py-1.5 border-b last:border-b-0">
              <Label htmlFor={field.key} className="w-48 text-muted-foreground shrink-0">
                {field.label}
                {field.required && <span className="text-destructive ml-1">*</span>}
              </Label>
              <div className="flex-1">
                {renderField(field, formData[field.key], handleChange)}
                {errors[field.key] && (
                  <p className="text-sm text-destructive mt-1">{errors[field.key]}</p>
                )}
              </div>
            </div>
          ))}

          <div className="flex justify-end gap-2 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={handleReset}
            >
              Reset
            </Button>
            {onCancel && (
              <Button type="button" variant="outline" onClick={onCancel}>
                Cancel
              </Button>
            )}
            <Button type="submit" disabled={loading}>
              {loading ? 'Saving...' : submitLabel}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
