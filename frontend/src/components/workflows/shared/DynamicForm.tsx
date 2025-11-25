import { useState, FormEvent } from 'react';
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

interface DynamicFormProps {
  schema: WorkflowConfig;
  initialData?: Record<string, unknown>;
  onSubmit: (data: Record<string, unknown>) => void;
  onCancel?: () => void;
  submitLabel?: string;
  loading?: boolean;
}

// Render input based on field type
function renderField(
  field: SchemaField,
  value: unknown,
  onChange: (key: string, value: string) => void
) {
  const stringValue = value !== null && value !== undefined ? String(value) : '';

  switch (field.type) {
    case 'select':
      return (
        <Select
          value={stringValue}
          onValueChange={(val) => onChange(field.key, val)}
        >
          <SelectTrigger>
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
          className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
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
        />
      );

    case 'datetime':
      return (
        <Input
          id={field.key}
          type="datetime-local"
          value={stringValue}
          onChange={(e) => onChange(field.key, e.target.value)}
        />
      );

    case 'phone':
      return (
        <Input
          id={field.key}
          type="tel"
          value={stringValue}
          onChange={(e) => onChange(field.key, e.target.value)}
          placeholder={field.label}
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
}: DynamicFormProps) {
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

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {fields.map((field) => (
        <div key={field.key} className="space-y-2">
          <Label htmlFor={field.key}>
            {field.label}
            {field.required && <span className="text-destructive ml-1">*</span>}
          </Label>
          {renderField(field, formData[field.key], handleChange)}
          {errors[field.key] && (
            <p className="text-sm text-destructive">{errors[field.key]}</p>
          )}
        </div>
      ))}

      <div className="flex gap-2 pt-4">
        <Button type="submit" disabled={loading}>
          {loading ? 'Saving...' : submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
