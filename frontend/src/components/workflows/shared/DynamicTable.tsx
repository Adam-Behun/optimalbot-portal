import { useState, useMemo } from 'react';
import { WorkflowConfig, Patient, SchemaField } from '@/types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ArrowUpDown, Phone, Trash2 } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

interface DynamicTableProps {
  schema: WorkflowConfig;
  patients: Patient[];
  onRowClick?: (patient: Patient) => void;
  loading?: boolean;
  onStartCalls?: (patients: Patient[]) => Promise<void>;
  onDeletePatients?: (patients: Patient[]) => Promise<void>;
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

type SortDirection = 'asc' | 'desc' | null;

export function DynamicTable({
  schema,
  patients,
  onRowClick,
  loading,
  onStartCalls,
  onDeletePatients,
}: DynamicTableProps) {
  // Get columns from schema, sorted by display_order
  const columns = schema.patient_schema.fields
    .filter((f) => f.display_in_list)
    .sort((a, b) => a.display_order - b.display_order);

  // Find the patient_name field for filtering
  const patientNameField = columns.find(f => f.key === 'patient_name' || f.key.includes('name'));

  // State
  const [nameFilter, setNameFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [sortField, setSortField] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);
  const [pageSize, setPageSize] = useState(10);
  const [pageIndex, setPageIndex] = useState(0);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [bulkActionLoading, setBulkActionLoading] = useState(false);

  // Filter and sort patients
  const filteredPatients = useMemo(() => {
    let result = [...patients];

    // Filter by name
    if (nameFilter && patientNameField) {
      const lower = nameFilter.toLowerCase();
      result = result.filter(p => {
        const value = p[patientNameField.key];
        return value && String(value).toLowerCase().includes(lower);
      });
    }

    // Filter by status
    if (statusFilter !== 'all') {
      result = result.filter(p => p.call_status === statusFilter);
    }

    // Sort
    if (sortField && sortDirection) {
      result.sort((a, b) => {
        const aVal = a[sortField] ?? '';
        const bVal = b[sortField] ?? '';
        const comparison = String(aVal).localeCompare(String(bVal));
        return sortDirection === 'asc' ? comparison : -comparison;
      });
    }

    return result;
  }, [patients, nameFilter, statusFilter, sortField, sortDirection, patientNameField]);

  // Pagination
  const pageCount = Math.ceil(filteredPatients.length / pageSize);
  const paginatedPatients = filteredPatients.slice(
    pageIndex * pageSize,
    (pageIndex + 1) * pageSize
  );

  // Selection
  const selectedPatients = patients.filter(p => selectedIds.has(p.patient_id));
  const allPageSelected = paginatedPatients.length > 0 &&
    paginatedPatients.every(p => selectedIds.has(p.patient_id));
  const somePageSelected = paginatedPatients.some(p => selectedIds.has(p.patient_id));

  const toggleSelectAll = () => {
    if (allPageSelected) {
      const newSelected = new Set(selectedIds);
      paginatedPatients.forEach(p => newSelected.delete(p.patient_id));
      setSelectedIds(newSelected);
    } else {
      const newSelected = new Set(selectedIds);
      paginatedPatients.forEach(p => newSelected.add(p.patient_id));
      setSelectedIds(newSelected);
    }
  };

  const toggleSelect = (patientId: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(patientId)) {
      newSelected.delete(patientId);
    } else {
      newSelected.add(patientId);
    }
    setSelectedIds(newSelected);
  };

  const handleSort = (field: string) => {
    if (sortField === field) {
      if (sortDirection === 'asc') {
        setSortDirection('desc');
      } else if (sortDirection === 'desc') {
        setSortField(null);
        setSortDirection(null);
      }
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const handleBulkStartCalls = async () => {
    if (!onStartCalls || selectedPatients.length === 0) return;
    setBulkActionLoading(true);
    try {
      await onStartCalls(selectedPatients);
      setSelectedIds(new Set());
    } finally {
      setBulkActionLoading(false);
    }
  };

  const handleBulkDelete = async () => {
    if (!onDeletePatients || selectedPatients.length === 0) return;
    setBulkActionLoading(true);
    setShowDeleteDialog(false);
    try {
      await onDeletePatients(selectedPatients);
      setSelectedIds(new Set());
    } finally {
      setBulkActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (patients.length === 0) {
    return (
      <div className="text-center py-12 border rounded-lg bg-card">
        <p className="text-muted-foreground">No patients found</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-4">
        <Input
          placeholder="Filter by patient name..."
          value={nameFilter}
          onChange={(e) => {
            setNameFilter(e.target.value);
            setPageIndex(0);
          }}
          className="max-w-sm"
        />
        <Select
          value={statusFilter}
          onValueChange={(value) => {
            setStatusFilter(value);
            setPageIndex(0);
          }}
        >
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Call Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Statuses</SelectItem>
            <SelectItem value="Not Started">Not Started</SelectItem>
            <SelectItem value="In Progress">In Progress</SelectItem>
            <SelectItem value="Completed">Completed</SelectItem>
            <SelectItem value="Failed">Failed</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Bulk Actions Bar */}
      {selectedPatients.length > 0 && (onStartCalls || onDeletePatients) && (
        <div className="flex items-center gap-2 p-4 bg-muted rounded-lg">
          <span className="text-sm font-medium">
            {selectedPatients.length} selected
          </span>
          <div className="flex gap-2 ml-auto">
            {onStartCalls && (
              <Button
                onClick={handleBulkStartCalls}
                disabled={bulkActionLoading}
                variant="default"
                size="sm"
              >
                <Phone className="mr-2 h-4 w-4" />
                Start Calls
              </Button>
            )}
            {onDeletePatients && (
              <Button
                onClick={() => setShowDeleteDialog(true)}
                disabled={bulkActionLoading}
                variant="destructive"
                size="sm"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete Selected
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              {(onStartCalls || onDeletePatients) && (
                <TableHead className="w-[50px]">
                  <Checkbox
                    checked={allPageSelected ? true : somePageSelected ? 'indeterminate' : false}
                    onCheckedChange={toggleSelectAll}
                    aria-label="Select all"
                  />
                </TableHead>
              )}
              {columns.map((field) => (
                <TableHead key={field.key}>
                  <Button
                    variant="ghost"
                    onClick={() => handleSort(field.key)}
                    className="h-auto p-0 font-medium hover:bg-transparent"
                  >
                    {field.label}
                    <ArrowUpDown className="ml-2 h-4 w-4" />
                  </Button>
                </TableHead>
              ))}
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort('call_status')}
                  className="h-auto p-0 font-medium hover:bg-transparent"
                >
                  Call Status
                  <ArrowUpDown className="ml-2 h-4 w-4" />
                </Button>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedPatients.map((patient) => (
              <TableRow
                key={patient.patient_id}
                data-state={selectedIds.has(patient.patient_id) && 'selected'}
                onClick={() => onRowClick?.(patient)}
                className={onRowClick ? 'cursor-pointer' : ''}
              >
                {(onStartCalls || onDeletePatients) && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={selectedIds.has(patient.patient_id)}
                      onCheckedChange={() => toggleSelect(patient.patient_id)}
                      aria-label="Select row"
                    />
                  </TableCell>
                )}
                {columns.map((field) => (
                  <TableCell key={field.key}>
                    {field.key === 'patient_name' ? (
                      <div className="font-medium">{formatValue(patient[field.key], field)}</div>
                    ) : (
                      formatValue(patient[field.key], field)
                    )}
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
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <div className="flex-1 text-sm text-muted-foreground">
          {selectedPatients.length} of {filteredPatients.length} row(s) selected.
        </div>
        <div className="flex items-center space-x-6 lg:space-x-8">
          <div className="flex items-center space-x-2">
            <p className="text-sm font-medium">Rows per page</p>
            <Select
              value={`${pageSize}`}
              onValueChange={(value) => {
                setPageSize(Number(value));
                setPageIndex(0);
              }}
            >
              <SelectTrigger className="h-8 w-[70px]">
                <SelectValue placeholder={pageSize} />
              </SelectTrigger>
              <SelectContent side="top">
                {[10, 20, 30, 40, 50].map((size) => (
                  <SelectItem key={size} value={`${size}`}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex w-[100px] items-center justify-center text-sm font-medium">
            Page {pageIndex + 1} of {pageCount || 1}
          </div>
          <div className="flex items-center space-x-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPageIndex(p => p - 1)}
              disabled={pageIndex === 0}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPageIndex(p => p + 1)}
              disabled={pageIndex >= pageCount - 1}
            >
              Next
            </Button>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {selectedPatients.length} patient(s)?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete the selected patient records.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBulkDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
