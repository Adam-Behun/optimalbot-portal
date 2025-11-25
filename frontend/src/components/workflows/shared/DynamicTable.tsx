import { WorkflowConfig, Patient, SchemaField } from '@/types';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

interface DynamicTableProps {
  schema: WorkflowConfig;
  patients: Patient[];
  onRowClick?: (patient: Patient) => void;
  loading?: boolean;
}

// Format value based on field type
function formatValue(value: unknown, field: SchemaField): string {
  if (value === null || value === undefined || value === '') {
    return '-';
  }

  switch (field.type) {
    case 'date':
      return new Date(value as string).toLocaleDateString();
    case 'datetime':
      return new Date(value as string).toLocaleString();
    case 'phone':
      return formatPhone(String(value));
    default:
      return String(value);
  }
}

// Format phone number for display
function formatPhone(phone: string): string {
  const cleaned = phone.replace(/\D/g, '');
  if (cleaned.length === 11 && cleaned.startsWith('1')) {
    return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
  }
  if (cleaned.length === 10) {
    return `(${cleaned.slice(0, 3)}) ${cleaned.slice(3, 6)}-${cleaned.slice(6)}`;
  }
  return phone;
}

// Get badge variant based on call status
function getStatusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'Completed':
    case 'Completed - Left VM':
      return 'default';
    case 'In Progress':
      return 'secondary';
    case 'Failed':
      return 'destructive';
    case 'Supervisor Requested':
    case 'Call Transferred':
      return 'outline';
    default:
      return 'secondary';
  }
}

export function DynamicTable({ schema, patients, onRowClick, loading }: DynamicTableProps) {
  // Get columns from schema, sorted by display_order
  const columns = schema.patient_schema.fields
    .filter((f) => f.display_in_list)
    .sort((a, b) => a.display_order - b.display_order);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (patients.length === 0) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="text-muted-foreground">No patients found</div>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((field) => (
            <TableHead key={field.key}>{field.label}</TableHead>
          ))}
          <TableHead>Call Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {patients.map((patient) => (
          <TableRow
            key={patient.patient_id}
            onClick={() => onRowClick?.(patient)}
            className={onRowClick ? 'cursor-pointer' : ''}
          >
            {columns.map((field) => (
              <TableCell key={field.key}>
                {formatValue(patient[field.key], field)}
              </TableCell>
            ))}
            <TableCell>
              <Badge variant={getStatusVariant(patient.call_status)}>
                {patient.call_status}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
