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
import ExcelJS from 'exceljs';

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

  // Excel/CSV Upload handlers
  const handleDownloadSample = async () => {
    // Create workbook and worksheet
    const workbook = new ExcelJS.Workbook();
    const worksheet = workbook.addWorksheet('Patients');

    // Add header row
    const headers = fields.map(f => f.key);
    worksheet.addRow(headers);

    // Create example data row
    const exampleRow = fields.map(f => {
      switch (f.type) {
        case 'date':
          return '1990-01-15';
        case 'datetime':
          return '2025-01-15T10:00';
        case 'phone':
          return '+11234567890';
        case 'select':
          return f.options?.[0] || `Example ${f.label}`;
        default:
          return `Example ${f.label}`;
      }
    });
    worksheet.addRow(exampleRow);

    // Set column widths for better readability
    worksheet.columns = fields.map(f => ({ width: Math.max(f.label.length, 20) }));

    // Generate buffer and download
    const buffer = await workbook.xlsx.writeBuffer();
    const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = URL.createObjectURL(blob);

    const link = document.createElement('a');
    link.href = url;
    link.download = 'patient_upload_example.xlsx';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !onBulkSubmit) return;

    const fileExtension = file.name.split('.').pop()?.toLowerCase();

    // Handle Excel files (.xlsx, .xls)
    if (fileExtension === 'xlsx' || fileExtension === 'xls') {
      try {
        const arrayBuffer = await file.arrayBuffer();
        const workbook = new ExcelJS.Workbook();
        await workbook.xlsx.load(arrayBuffer);

        // Read first sheet
        const worksheet = workbook.worksheets[0];
        if (!worksheet) {
          toast.error('Excel file has no worksheets');
          return;
        }

        // Get headers from first row
        const headerRow = worksheet.getRow(1);
        const headers: string[] = [];
        headerRow.eachCell((cell, colNumber) => {
          headers[colNumber - 1] = String(cell.value || '');
        });

        // Convert rows to objects
        const patients: Record<string, unknown>[] = [];
        worksheet.eachRow((row, rowNumber) => {
          if (rowNumber === 1) return; // Skip header row

          const patient: Record<string, unknown> = {};
          row.eachCell((cell, colNumber) => {
            const header = headers[colNumber - 1];
            if (header) {
              patient[header] = cell.value;
            }
          });

          // Only add if row has data
          if (Object.keys(patient).length > 0) {
            patients.push(patient);
          }
        });

        if (patients.length === 0) {
          toast.error('Excel file is empty');
          return;
        }

        toast.info(`Processing ${patients.length} patients...`);
        await onBulkSubmit(patients);

        // Reset file input
        if (fileInputRef.current) {
          fileInputRef.current.value = '';
        }
      } catch (error: unknown) {
        console.error('Error uploading Excel:', error);
        const errorMsg = error instanceof Error ? error.message : 'Failed to upload Excel file';
        toast.error(errorMsg);
      }
      return;
    }

    // Handle CSV files
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
        } catch (error: unknown) {
          console.error('Error uploading CSV:', error);
          const errorMsg = error instanceof Error ? error.message : 'Failed to upload CSV file';
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
      {/* Excel/CSV Upload Buttons */}
      {showCsvUpload && onBulkSubmit && (
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={handleDownloadSample}
            className="w-full sm:w-60"
          >
            <Download className="mr-2 h-5 w-5" />
            Download Sample .xlsx
          </Button>
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={handleUploadClick}
            className="w-full sm:w-60"
          >
            <Upload className="mr-2 h-5 w-5" />
            Upload Excel or CSV
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls,.csv"
            className="hidden"
            onChange={handleFileChange}
          />
        </div>
      )}

      {/* Form */}
      <div className="bg-card rounded-lg border">
        <form onSubmit={handleSubmit} className="p-4 space-y-0">
          {fields.map((field) => (
            <div key={field.key} className="flex flex-col gap-1 py-2 border-b last:border-b-0 sm:flex-row sm:items-center sm:gap-0 sm:py-1.5">
              <Label htmlFor={field.key} className="text-muted-foreground sm:w-48 sm:shrink-0">
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
